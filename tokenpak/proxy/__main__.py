# Allow `python -m tokenpak.proxy` to start the proxy server.
# For the per-module form use `python -m tokenpak.proxy.server`.
from tokenpak.proxy.server import main

if __name__ == "__main__":
    main()
