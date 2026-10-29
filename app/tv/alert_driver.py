"""
=========================================================
Datei:      app/tv/alert_driver.py
Zweck:      Playwright-Alert-Dialog — Schema-A Webhook auf TradingView setzen.
=========================================================
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.tv.selector_manager import get_selector_manager
from app.tv.strategy_tester_driver import DriverError

logger = logging.getLogger("app.tv.alert_driver")


class FakeAlertDriver:
    """Test-Seam: merkt sich upsert/enable/disable ohne Browser."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.alerts: Dict[str, Dict[str, Any]] = {}

    def upsert_alert(self, name: str, symbol: str, interval: Any,
                     webhook_url: str, message: str) -> Dict[str, Any]:
        rec = {
            "tv_alert_id": f"tv:{name}",
            "name": name, "symbol": symbol, "interval": str(interval),
            "webhook_url": webhook_url, "message": message, "enabled": False,
        }
        self.alerts[name] = rec
        self.calls.append(f"upsert_alert:{name}")
        return dict(rec)

    def enable_alert(self, tv_alert_id: str) -> Dict[str, Any]:
        self.calls.append(f"enable_alert:{tv_alert_id}")
        rec = self._find(tv_alert_id)
        if rec is not None:
            rec["enabled"] = True
        return {"ok": True, "tv_alert_id": tv_alert_id}

    def disable_alert(self, tv_alert_id: str) -> Dict[str, Any]:
        self.calls.append(f"disable_alert:{tv_alert_id}")
        rec = self._find(tv_alert_id)
        if rec is not None:
            rec["enabled"] = False
        return {"ok": True, "tv_alert_id": tv_alert_id}

    def delete_alert(self, tv_alert_id: str) -> Dict[str, Any]:
        self.calls.append(f"delete_alert:{tv_alert_id}")
        for name, rec in list(self.alerts.items()):
            if rec.get("tv_alert_id") == tv_alert_id or rec.get("name") == tv_alert_id:
                self.alerts.pop(name, None)
        return {"ok": True, "tv_alert_id": tv_alert_id}

    def list_alerts(self) -> List[Dict[str, Any]]:
        self.calls.append("list_alerts")
        return [dict(v) for v in self.alerts.values()]

    def _find(self, key: str) -> Optional[Dict[str, Any]]:
        if key in self.alerts:
            return self.alerts[key]
        for rec in self.alerts.values():
            if rec.get("tv_alert_id") == key or rec.get("name") == key:
                return rec
        return None


class PlaywrightAlertDriver:
    """Oeffnet den TV-Alert-Dialog (Order fills only + webhook) via Selektoren."""

    def __init__(self, tester=None, selectors=None) -> None:
        self.tester = tester
        self.selectors = selectors or get_selector_manager()
        self.calls: List[str] = []

    def upsert_alert(self, name: str, symbol: str, interval: Any,
                     webhook_url: str, message: str) -> Dict[str, Any]:
        self.calls.append(f"upsert_alert:{name}")
        tester = self._tester()
        if hasattr(tester, "upsert_alert") and tester is not self:
            return tester.upsert_alert(name, symbol, interval, webhook_url, message)
        if hasattr(tester, "open_chart"):
            tester.open_chart(symbol, interval)
        self._click("alerts", "create_button")
        self._click_optional("alerts", "condition_order_fills")
        self._fill("alerts", "message_field", message)
        self._click_optional("alerts", "webhook_checkbox")
        self._fill("alerts", "webhook_url_field", webhook_url)
        self._click_optional("alerts", "expiration_open_ended")
        self._click("alerts", "submit_button")
        return {
            "tv_alert_id": name,
            "name": name, "symbol": symbol, "interval": str(interval),
            "webhook_url": webhook_url,
        }

    def enable_alert(self, tv_alert_id: str) -> Dict[str, Any]:
        self.calls.append(f"enable_alert:{tv_alert_id}")
        tester = self._tester()
        if hasattr(tester, "enable_alert") and tester is not self:
            return tester.enable_alert(tv_alert_id)
        self._click("alerts", "manage_panel")
        self._click("alerts", "alert_toggle")
        return {"ok": True, "tv_alert_id": tv_alert_id}

    def disable_alert(self, tv_alert_id: str) -> Dict[str, Any]:
        self.calls.append(f"disable_alert:{tv_alert_id}")
        tester = self._tester()
        if hasattr(tester, "disable_alert") and tester is not self:
            return tester.disable_alert(tv_alert_id)
        self._click("alerts", "manage_panel")
        self._click("alerts", "alert_toggle")
        return {"ok": True, "tv_alert_id": tv_alert_id}

    def delete_alert(self, tv_alert_id: str) -> Dict[str, Any]:
        self.calls.append(f"delete_alert:{tv_alert_id}")
        tester = self._tester()
        if hasattr(tester, "delete_alert") and tester is not self:
            return tester.delete_alert(tv_alert_id)
        self._click("alerts", "manage_panel")
        return {"ok": True, "tv_alert_id": tv_alert_id}

    def list_alerts(self) -> List[Dict[str, Any]]:
        self.calls.append("list_alerts")
        tester = self._tester()
        if hasattr(tester, "list_alerts") and tester is not self:
            return tester.list_alerts()
        return []

    def _tester(self):
        if self.tester is None:
            from app.tv.strategy_tester_driver import PlaywrightStrategyTesterDriver
            self.tester = PlaywrightStrategyTesterDriver()
        return self.tester

    def _click(self, category: str, name: str) -> Any:
        tester = self._tester()
        if hasattr(tester, "click_element_with_fallback"):
            return tester.click_element_with_fallback(category, name)
        raise DriverError(f"{bp.SELECTOR_ERROR_CODE}: {category}.{name}", bp.SELECTOR_ERROR_CODE)

    def _click_optional(self, category: str, name: str) -> None:
        try:
            self._click(category, name)
        except Exception as exc:
            logger.debug("optional alert click %s.%s skipped: %s", category, name, exc)

    def _fill(self, category: str, name: str, value: str) -> None:
        tester = self._tester()
        page = getattr(tester, "page", None)
        if page is None:
            self._click(category, name)
            return
        last_exc: Optional[Exception] = None
        for selector in self.selectors.get(category, name):
            try:
                loc = page.locator(selector).first
                loc.wait_for(state="visible", timeout=4000)
                loc.fill(value)
                return
            except Exception as exc:
                last_exc = exc
                continue
        raise DriverError(
            f"{bp.SELECTOR_ERROR_CODE}: {category}.{name} fill failed ({last_exc})",
            bp.SELECTOR_ERROR_CODE,
        )


def alert_driver_from_tester(tester) -> Any:
    if tester is not None and hasattr(tester, "upsert_alert"):
        return tester
    return PlaywrightAlertDriver(tester=tester)
