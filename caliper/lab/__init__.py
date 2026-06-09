"""Lab-side (data-plane) assets — commodity, no product IP.

The runner here executes dispatched steps on a lab server inside the lab pack
(container or conda env). It holds no trust/calibration logic — that lives in the
control plane (the brain). Safe to be public / shipped as a binary image.
"""
