Non-2xx responses discard the body, making the recipe simulate unreachable

---

## Summary

`_handle_response` treats every status outside `{200, 201, 204}` as an error
and keeps only a message string. The response body is discarded before any
caller can see it.

This makes one documented VergeOS feature unreachable through the SDK: a
**recipe simulation** succeeds and returns its entire report on **HTTP 405**.
Through pyvergeos that becomes `APIError('Simulation complete',
status_code=405)` and the 28-entry report is gone.

Affects **1.2.3 and 1.2.4** (verified on both), against VergeOS **26.1.8**.

## Code location

```python
# pyvergeos/client.py:585-608
def _handle_response(self, response):
    if response.status_code in HTTP_SUCCESS_CODES:      # {200, 201}
        ...
    if response.status_code == HTTP_NO_CONTENT:          # 204
        return None

    error_message = self._extract_error_message(response)   # :597 -- returns str
    ...
    else:
        raise APIError(error_message, status_code=response.status_code)   # :608
```

`_extract_error_message` (`client.py:610`) returns a `str`, and
`APIError.__init__` (`exceptions.py:37`) accepts only `message` and
`status_code`. There is nowhere for a body to go.

Related: `HTTP_SUCCESS_CODES` (`constants.py:76-81`) is `{200, 201}` only, so
**202 Accepted is also treated as an error** — see "Secondary issue" below.

## What a recipe simulation is

Setting `"simulate": true` on `POST /api/v4/vm_recipe_instances` asks VergeOS
to walk every step the recipe would run and report what it would do, without
doing it. It is the API form of the **Simulate Recipe** button in the UI,
which the documentation describes as letting you *"test field validation and
create sample answer files"*.

It returns three things worth having: a step-by-step build log, the resolved
answers including keys the recipe worked out for itself, and the fully
rendered cloud-init.

## Reproduction

### Raw HTTP

```bash
curl -sk -u "$VERGEOS_USERNAME:$VERGEOS_PASSWORD" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"recipe":"<recipe $key>","name":"probe","simulate":true,
       "answers":{"HOSTNAME":"probe","USER":"ops","PASSWORD":"...",
                  "YB_CPU_CORES":1,"YB_RAM":1024,
                  "YB_NIC_ETH0":<vnet $key>,"SELECT_OS_TIER":1}}' \
  -w '\nHTTP %{http_code}\n' \
  "https://$VERGEOS_HOST/api/v4/vm_recipe_instances"
```

```
HTTP 405
err           : 'Simulation complete'
response keys : ['answers', 'cloudinit_files', 'logs']
log entries   : 28
cloudinit     : ['/meta-data', '/network-config', '/user-data']
predicted keys: YB_VM_KEY=36 YB_MACHINE_KEY=32
```

### Through pyvergeos

```python
try:
    c._request("POST", "vm_recipe_instances", json_data=body)
except APIError as e:
    print(type(e).__name__, repr(str(e)), e.status_code)
    print([a for a in dir(e) if not a.startswith("_")])
```

```
APIError 'Simulation complete' 405
['add_note', 'args', 'status_code', 'with_traceback']
```

No body. And there is no alternative route — `vm_recipe_instances.create()`
does not accept a `simulate` argument, and the string `simulate` does not
appear anywhere in the package.

### The simulation is a genuine dry run

Confirmed with a control reading (two identical measurements with no action
between them, then the simulate):

```
before  : vms=48 machines=63 machine_drives=49 vm_recipe_logs=359 vm_recipe_instances=1
control : identical
after   : identical
```

Nothing is created, and repeated runs report identical predicted keys.

## Expected behaviour

A caller should be able to reach the response body of a non-2xx reply, so it
can decide for itself what a given status means for a given endpoint.

## Suggested fix

Attach the parsed body to the exception. Two lines, backward compatible —
`str(e)` and `e.status_code` are unchanged, so no existing caller breaks:

```python
# in _handle_response, before raising
body = None
try:
    body = response.json()
except ValueError:
    body = None
...
exc = APIError(error_message, status_code=response.status_code)
exc.body = body
raise exc
```

Verified by applying exactly this at runtime and re-running the identical SDK
call:

```
before:  str(e)='Simulation complete'  status=405  attrs=[args, status_code]
after :  status=405  err='Simulation complete'  log entries=28
         cloudinit=['/meta-data', '/network-config', '/user-data']
```

A `simulate: bool = False` parameter on
`VmRecipeInstanceManager.create()` would complete the feature.

## Secondary issue: 202 Accepted

`HTTP_SUCCESS_CODES` is `{200, 201}`. **202 Accepted** — the conventional
reply for an accepted long-running request — is handled as an error and its
body discarded.

Nothing observed on VergeOS 26.1.8 returns 202. Across 322 table GETs, 46
`*_actions` POSTs and the recipe create/simulate paths, only `200, 201, 404,
405, 422` were seen. So this is a latent fragility rather than a live bug, but
it is the same root cause and worth fixing in the same change.

## Scope

Measured, so the blast radius is clear rather than assumed:

- **all 322 table GETs return 2xx** — no read is affected;
- **all 46 `*_actions` endpoints** return `{'err': '...'}` and nothing else on
  a malformed request, so genuine errors lose no information today;
- **one confirmed affected call**: the recipe simulate;
- `tenant_recipe_instances` is presumed identical but untested — no tenant
  recipes existed on the test system.

Finding every "success reported on a non-2xx status" exhaustively would
require successfully executing every action, which was not done. The list is
complete for reads and malformed requests only.

## Possibly also worth raising with the platform team

A *successful* simulation answering **405 Method Not Allowed** is surprising;
200 would be conventional. If the API changed, this SDK issue would largely
resolve itself — though the 202 gap and the general "caller cannot see the
body" limitation would remain.

## Environment

- pyvergeos 1.2.3 and 1.2.4 (identical behaviour)
- VergeOS 26.1.8
- Python 3.14
