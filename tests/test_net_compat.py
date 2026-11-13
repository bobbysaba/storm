import socket

import net_compat


def _info(family, addr):
    return (family, socket.SOCK_STREAM, 6, "", (addr, 443))


def test_ipv4_listed_first_and_ipv6_kept(monkeypatch):
    v6a, v4a, v6b, v4b = (_info(socket.AF_INET6, "2001:db8::1"), _info(socket.AF_INET, "192.0.2.1"),
                          _info(socket.AF_INET6, "2001:db8::2"), _info(socket.AF_INET, "192.0.2.2"))
    monkeypatch.setattr(net_compat, "_original_getaddrinfo", lambda *a, **k: [v6a, v4a, v6b, v4b])
    # IPv4 first, each family keeping its own order; nothing dropped
    assert net_compat._ipv4_first_getaddrinfo("host", 443) == [v4a, v4b, v6a, v6b]


def test_ipv6_only_host_unchanged(monkeypatch):
    only = [_info(socket.AF_INET6, "2001:db8::1"), _info(socket.AF_INET6, "2001:db8::2")]
    monkeypatch.setattr(net_compat, "_original_getaddrinfo", lambda *a, **k: list(only))
    assert net_compat._ipv4_first_getaddrinfo("host", 443) == only


def test_prefer_ipv4_installs_the_lookup(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)   # restored after the test
    net_compat.prefer_ipv4()
    assert socket.getaddrinfo is net_compat._ipv4_first_getaddrinfo
