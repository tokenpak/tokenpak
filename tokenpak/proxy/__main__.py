# Allow `python -m tokenpak.proxy` to start the proxy server.
# For the per-module form use `python -m tokenpak.proxy.server`.

# The active profile is published into the environment here, in the proxy
# process, before the server modules import. Proxy config no longer does this
# at import time — that leaked the profile into every CLI verb that touched
# proxy config — but the server subsystems that read TOKENPAK_MODE and
# TOKENPAK_CAPSULE_BUILDER straight from os.environ still need it applied for
# the process the profile actually configures.
from tokenpak.proxy.config import apply_profile_to_environ

apply_profile_to_environ()

from tokenpak.proxy.server import main  # noqa: E402

if __name__ == "__main__":
    main()
