"""One process-wide lock for PyMuPDF operations dispatched to worker threads."""

import threading

PDF_PARSE_LOCK = threading.RLock()
