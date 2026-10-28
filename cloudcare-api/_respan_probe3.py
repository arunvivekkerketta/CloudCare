import os

os.environ["RESPAN_API_KEY"] = "test-key-for-local-check"
os.environ["RESPAN_BASE_URL"] = "http://127.0.0.1:9/api"

from respan import Respan, get_client, workflow

respan = Respan(app_name="cloudcare-probe")
print("idle trace id:", get_client().get_current_trace_id())


@workflow(name="consultation")
def run():
    return get_client().get_current_trace_id()


print("inside workflow:", run())
print("idle again:", get_client().get_current_trace_id())
