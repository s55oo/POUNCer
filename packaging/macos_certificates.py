"""Give the frozen macOS Python a portable CA bundle before app imports."""

import os
import sys

if sys.platform == "darwin":
    # Respect an explicitly configured trust store (e.g. a company CA bundle).
    os.environ.setdefault(
        "SSL_CERT_FILE", os.path.join(sys._MEIPASS, "certificates", "cacert.pem")
    )
