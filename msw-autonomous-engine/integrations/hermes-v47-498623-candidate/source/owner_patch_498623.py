"""Build a source-pinned V47 owner CLI grant-routing patch candidate.

The patch is separate from the deployed V46 owner. The V47 command delegates to
the fail-closed owner route, which refuses to run until the exact 498623
snapshot, STOP/lease/effect fences and append-only owner ledger are wired.
"""
import hashlib

SOURCE_SHA256 = '6fea6bf77ba4b1021fe69756b1c73ca2cbc85267db5d21ae89e82716a005a7c0'
CHOICES = b'"reconcile-ack-profile", "report", "parent-proof"))'
HOOK_POINT = b'        if args.command == "reconcile-ack-profile":\n'
HOOK = (b'        if args.command == "grant-498623":\n'
        b'            import sys\n'
        b'            import owner_grant_route_498623\n'
        b'            result = owner_grant_route_498623.issue_from_owner(sys.modules[__name__])\n'
        b'        elif args.command == "preflight-498623":\n'
        b'            import sys\n'
        b'            import owner_preflight_498623\n'
        b'            result = owner_preflight_498623.preflight(sys.modules[__name__])\n'
        b'        elif args.command == "status-498623":\n'
        b'            import sys\n'
        b'            import owner_status_498623\n'
        b'            result = owner_status_498623.status(sys.modules[__name__])\n'
        b'        elif args.command == "final-readback-498623":\n'
        b'            import sys\n'
        b'            import owner_final_readback_498623\n'
        b'            result = owner_final_readback_498623.status(sys.modules[__name__])\n'
        b'        elif args.command == "reconcile-ack-profile":\n')


def build(source):
    if not isinstance(source, bytes) or hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError('owner_source_pin_mismatch')
    if source.count(CHOICES) != 1 or source.count(HOOK_POINT) != 1:
        raise ValueError('owner_hook_point_mismatch')
    source = source.replace(
        CHOICES,
        b'"reconcile-ack-profile", "report", "parent-proof", "grant-498623", "preflight-498623", "status-498623", "final-readback-498623"))')
    source = source.replace(HOOK_POINT, HOOK)
    compile(source, 'recover_blocked_supplement_owner.py', 'exec')
    return source
