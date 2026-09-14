# ONNX-Modelle (§38)

Dieses Verzeichnis hält die ONNX-Artefakte, die der Netron-Inspector auf
Port 8082 anzeigt (`NETRON_MODELS_DIR = "./models"`).

- Default-Modell: `regime_classifier.onnx` (§21 Self-Optimizing ONNX)
- Die `.onnx`-Dateien selbst sind **nicht** versioniert (siehe `.gitignore`);
  sie entstehen beim Training bzw. kommen aus der Model-Registry.
- Nur `.onnx` wird geladen — `NetronVisualizerService.resolve_model()` blockt
  andere Endungen und Pfade ausserhalb dieses Verzeichnisses (§38.7).
