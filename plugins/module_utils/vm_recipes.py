# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""pyvergeos glue for the recipe modules.

Everything here touches the SDK or the network. The answer-resolution logic
it feeds lives in module_utils/recipe_answers.py, which is deliberately free
of both.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# Recipe question fields the resolver needs that are not in the SDK's default
# projection for a table-backed question's option lookup. The SDK's own
# defaults already carry type/default/required/min/max/regex/table/fields/
# filter/section_name, so this is only about what the OPTION rows look like.
DEFAULT_OPTION_FIELDS = "$key,$display"


def resolve_recipe(client, name, catalog=None):
    """Find exactly one recipe by name, optionally narrowed by catalog name.

    Returns (recipe_row, error_message). Exactly one of the two is falsy.

    The recipe is found by listing and matching in Python rather than with a
    server-side ``name eq '...'`` filter. That began as a deliberate dodge of
    an unsettled question: pyvergeos escaped ``'`` SQL-style by doubling it,
    while the VergeOS 26.1.8 measurements recorded backslash-escaping as the
    form the platform accepts. Both could not be right.

    It is settled now. Backslash is correct, the doubled form returns
    ``{"err": "Invalid argument"}``, and pyvergeos 1.2.5 switched to a shared
    ``quote_value()`` that emits backslash. Re-verified on 1.2.7 against a
    live 26.1.8 system on 2026-09-21. The client-side match is retained
    because it is cheap here and changing it is a behaviour change, not
    because the question is still open.

    Getting it wrong does not raise -- the query silently matches nothing, or
    returns an error document that a caller counts as a result row. Matching
    client-side sidesteps the question entirely, and a recipe catalog is small
    enough (tens of rows) that the unfiltered list costs nothing. Revisit only
    once the escape has been confirmed live.
    """
    rows = [dict(r) for r in client.vm_recipes.list()]

    matches = [r for r in rows if r.get('name') == name]

    if catalog:
        matches = [r for r in matches
                   if (r.get('catalog_display') or '') == catalog]

    if len(matches) == 1:
        return matches[0], None

    where = " in catalog '%s'" % catalog if catalog else ""
    if not matches:
        return None, (
            "no recipe named '%s'%s. Check the name (it is matched exactly, "
            "including case and punctuation) and that the recipe is "
            "downloaded. Recipes present: %s"
            % (name, where,
               ", ".join(sorted(repr(r.get('name')) for r in rows)[:20]) or "none"))
    return None, (
        "%d recipes named '%s'%s. Set 'catalog' to disambiguate; the "
        "candidates are in catalogs %s"
        % (len(matches), name, where,
           ", ".join(sorted(repr(r.get('catalog_display')) for r in matches))))


def fetch_questions(client, recipe_key):
    """The recipe's question set, in the order the UI presents it.

    The SDK sorts by +orderid and its default field projection already carries
    everything the resolver reads, so no explicit field list is needed.
    """
    return [dict(q) for q in client.recipe_questions.list(
        recipe_ref="vm_recipes/%s" % recipe_key)]


def fetch_networks(client):
    """Networks, so a network-type answer can be given by name."""
    return [dict(n) for n in client.networks.list()]


def fetch_options(client, needs_options):
    """Resolve the valid values for each table-backed question.

    ``needs_options`` is the resolver's own output: one entry per row/list/
    cluster question, carrying the table and the field spec the UI renders its
    options with.

    Without this, a missing row-type answer surfaces as a bare "Missing
    required answer" -- or worse, an empty value that fails mid-deploy, which
    is the stock SELECT_OS_TIER / preferred_tier trap.
    """
    options = {}
    for spec in needs_options or []:
        params = {'fields': spec.get('fields') or DEFAULT_OPTION_FIELDS}
        if spec.get('filter'):
            params['filter'] = spec['filter']
        rows = client._request('GET', spec['table'], params=params)
        # A filter matching nothing returns {"$count": 0}, not [].
        options[spec['name']] = rows if isinstance(rows, list) else []
    return options


# HTTP statuses pyvergeos treats as success. Anything else goes down its
# error path, where the body is reduced to a message string.
SUCCESS = (200, 201, 202, 204)


def raw_request(client, method, endpoint, json_data=None, params=None):
    """Issue a request and hand back ``(status_code, document)`` unfiltered.

    pyvergeos ``_request`` dispatches on the HTTP status and, for anything
    non-2xx, raises with only ``_extract_error_message``'s string. That is a
    reasonable default and wrong for at least one endpoint that matters: the
    recipe simulate answers on **HTTP 405** with the full simulation log in
    the body (measured on VergeOS 26.1.8). Through the SDK that call raises
    ``APIError('Simulation complete', status_code=405)`` and the 28-entry log
    -- the entire point of a simulate -- is unrecoverable.

    So the status has to be read rather than trusted, which means reaching
    past ``_request`` to the session it uses. Kept to this one function; every
    caller that needs the body of a non-2xx response comes through here.

    Does not raise for HTTP status. It does not decide what a status means,
    because "405 is a failure" is exactly the assumption being corrected.
    Callers decide. Transport failures (timeout, connection refused) still
    raise out of ``requests``.
    """
    connection = getattr(client, '_connection', None)
    session = getattr(connection, 'session', None)
    if session is None:
        raise RuntimeError(
            'no active pyvergeos session; the client is not connected')

    url = '%s/%s' % (connection.api_base_url, endpoint)
    response = session.request(
        method=method, url=url, params=params, json=json_data,
        timeout=getattr(client, '_timeout', None) or 60)

    document = None
    if response.text:
        try:
            document = response.json()
        except ValueError:
            # A body that is not JSON is still evidence; losing it here is how
            # a proxy error page becomes a bare status code.
            document = {'err': response.text[:2000]}
    return response.status_code, document


def post_instance(client, recipe_key, name, answers,
                  auto_update=False, simulate=False):
    """POST a recipe instance, returning ``(status_code, document)``.

    This is the collection's only private-SDK path, kept in one place on
    purpose. Three things force it:

    1. pyvergeos has no ``simulate`` support at all -- ``create()`` never
       sends the flag -- and the server-side simulate is the only real
       preflight a recipe deploy has.
    2. ``create()`` discards the POST body, then re-fetches the instance. The
       body is where the key of the VM that was just built is reported
       (``response.vm``), so going through ``create()`` means inferring the
       identity of the new VM from its name instead of being told it.
    3. The simulate replies on a non-2xx status, so even ``_request`` throws
       the document away. See ``raw_request``.

    When a released pyvergeos grows a ``simulate`` argument that returns the
    raw document, this function is the only thing that needs to change.
    """
    body = {'recipe': recipe_key, 'name': name, 'answers': answers}
    if simulate:
        body['simulate'] = True
    if auto_update:
        body['auto_update'] = True
    return raw_request(client, 'POST', 'vm_recipe_instances', json_data=body)


def simulate_transport_error(status, document):
    """Why a simulate POST should be treated as a transport failure, or ''.

    A simulate answering on HTTP 405 with a log document is the normal,
    measured behaviour -- so status alone cannot be the test, and neither can
    "non-2xx means failure". What distinguishes a real failure is the absence
    of a simulation document: an auth rejection or a bad recipe key comes back
    with no ``response`` body to scan.

    Returned as a message rather than raised so the caller can report the
    status it actually saw instead of a generic API error.
    """
    if status in SUCCESS:
        return ''
    if isinstance(document, dict) and isinstance(document.get('response'), dict):
        return ''
    detail = ''
    if isinstance(document, dict):
        detail = str(document.get('err') or document)[:400]
    elif document is not None:
        detail = str(document)[:400]
    return ('the simulate POST failed at the transport level: HTTP %s%s'
            % (status, (' -- %s' % detail) if detail else ''))


def deployed_vm_key(document):
    """The key of the VM a deploy POST reported building, or ''.

    Tolerant by design: this runs immediately after a deploy that has already
    been accepted, and a missing field here must not turn a successful deploy
    into a traceback.
    """
    if not isinstance(document, dict):
        return ''
    response = document.get('response')
    if not isinstance(response, dict):
        return ''
    return str(response.get('vm') or '')


def find_vm_by_name(client, name):
    """An existing non-snapshot VM of this name, or None.

    Matched client-side for the same escaping reason as resolve_recipe(), and
    for one more: a refused filter returns ``{"err": ...}``, a single-key
    mapping that reads as one result row. That exact confusion made the role
    this module replaces announce "the VM already exists" and do nothing at
    all, for any name containing an apostrophe.
    """
    for row in client.vms.list():
        row = dict(row)
        if row.get('name') != name:
            continue
        # is_snapshot is absent on some projections; only skip a row that
        # positively says it is a snapshot.
        if row.get('is_snapshot'):
            continue
        return row
    return None
