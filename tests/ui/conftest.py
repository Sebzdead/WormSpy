import os

# Run Qt headless in tests and CI; must be set before QApplication exists.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
