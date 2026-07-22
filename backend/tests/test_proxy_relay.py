from pathlib import Path

from app.proxy_relay import _linux_default_gateway, _resolve_upstream_host


def test_linux_default_gateway_reads_proc_route(tmp_path: Path) -> None:
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t00000000\t01901EAC\t0003\t0\t0\t0\t00000000\n",
        encoding="ascii",
    )

    assert _linux_default_gateway(str(route)) == "172.30.144.1"


def test_resolve_upstream_host_preserves_explicit_address() -> None:
    assert _resolve_upstream_host("192.0.2.10") == "192.0.2.10"
