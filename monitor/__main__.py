import sys
from monitor.service import main

main(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
