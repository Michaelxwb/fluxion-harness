def test_egress_admin_rules_json_is_validated_and_round_trips():
    from muad_contracts.platform_settings import egress_rules_from_settings, parse_platform_settings

    rules_json = (
        '[{"method":"GET",'
        '"path_template":"/assets/{id}","target_instance_id":"assets",'
        '"route_id":"GET /assets","max_requests":50,"window_requests":50,'
        '"window_seconds":60,"max_inflight":4}]'
    )
    settings = parse_platform_settings(
        {
            "egress": {
                "enabled": True,
                "rules_json": rules_json,
            }
        }
    )
    rules = egress_rules_from_settings(settings.egress)
    assert rules[0]["route_id"] == "GET /assets/{id}"
