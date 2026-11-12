import os
import subprocess
from pathlib import Path

php = os.environ.get("MSW_TEST_PHP", "php")
harness = str(Path(__file__).with_name("test-transport-flow.php"))
aux = "msw-render-auxseo-20261107-2032"
central = "msw-render-central-1"


def run(version, action, node, cpu, ram, mode="fresh"):
    p = subprocess.run([php, harness, version, action, node, str(cpu), str(ram), mode],
                       capture_output=True, text=True, timeout=8, check=True)
    return p.stdout.strip()


for action, node, cpu, ram in [
    ("claim", central, 99, 99),
    ("complete", aux, 99, 99),
    ("status", aux, 99, 99),
    ("heartbeat", aux, 99, 99),
    ("claim", aux, 20, 20),
]:
    assert run("candidate", action, node, cpu, ram) == run("original", action, node, cpu, ram)

for cpu, ram, mode in [(70, 10, "fresh"), (10, 70, "fresh"), (20, 20, "stale"), (20, 20, "missing")]:
    result = run("candidate", "claim", aux, cpu, ram, mode)
    assert "aux_origin_pressure_or_unavailable" in result and "SIM_STATUS=503" in result

result = run("candidate", "claim", "deny", 99, 99)
assert "bad_signature" in result and "SIM_STATUS=401" in result
print("PASS: five unchanged paths, four blocked conditions, rejected authentication")
