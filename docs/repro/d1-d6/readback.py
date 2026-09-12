"""Independent read-back: raw HTTP, no SDK, no collection code.

Deliberately does not use pyvergeos or any collection module, so a
read-back cannot inherit a bug from the thing under test.
"""
import json, os, ssl, sys, urllib.request, urllib.parse, base64

def get(endpoint):
    host = os.environ["VERGEOS_HOST"]
    if "?" in endpoint:
        path, query = endpoint.split("?", 1)
        query = urllib.parse.quote(query, safe="=&,$")
        endpoint = path + "?" + query
    url = "https://%s/api/v4/%s" % (host, endpoint)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url)
    auth = base64.b64encode(
        ("%s:%s" % (os.environ["VERGEOS_USERNAME"], os.environ["VERGEOS_PASSWORD"])).encode()
    ).decode()
    req.add_header("Authorization", "Basic " + auth)
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read().decode())

if __name__ == "__main__":
    print(json.dumps(get(sys.argv[1])))
