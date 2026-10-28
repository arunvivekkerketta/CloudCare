import time

# Case 1: decorators imported and applied BEFORE any Respan() init.
from respan import task, workflow


@workflow(name="probe_workflow")
def run(a: int, b: int) -> int:
    return step(a) + b


@task(name="probe_step")
def step(a: int) -> int:
    return a * 2


print("case1 (no init):", run(2, 3))

print("case2 (after init):")
from respan import Respan

t0 = time.time()
client = Respan(app_name="cloudcare-probe")
print("init seconds:", round(time.time() - t0, 3))
print("result:", run(2, 3))
t0 = time.time()
if hasattr(client, "flush"):
    client.flush()
print("flush seconds:", round(time.time() - t0, 3))
