"""利用者の接続元（IP）を読む。登録の「接続元ごとに1人」と、ログインの回数制限が使う。

- 既定は、つないできた相手の IP（REMOTE_ADDR）。
- リバースプロキシ（Caddy・nginx・Cloudflare Tunnel など）の後ろでは、設定
  HAKONIWA_CLIENT_IP_HEADER で、プロキシが書き込む見出し（HTTP_CF_CONNECTING_IP・
  HTTP_X_REAL_IP・HTTP_X_FORWARDED_FOR）を読む。
  ただし見出しは利用者が自由に書けるので、つないできた相手が HAKONIWA_TRUSTED_PROXIES にあるときだけ
  信じる。ないときは見出しを捨てて REMOTE_ADDR を使う（偽の見出しで回数制限をすり抜けられない）。
- 見出しが「a, b, c」のように複数の IP を持つとき（X-Forwarded-For）は、右から見て、信頼する中継元
  でない最初の IP を接続元にする。左の端は利用者が書ける値なので使わない。
- IPv6 は、1 つの回線に配られる /64 の先頭にそろえる（同じ人が /64 の中で接続元を変えて、制限を
  すり抜けないように）。IPv4 を IPv6 の形で書いたものは IPv4 に直す。回数制限の単位は、この文字。
- 読めないときは空文字。呼ぶ側が扱いを決める（ログインは UNKNOWN_BUCKET に束ねて数える。
  登録は止めない）。
"""

from __future__ import annotations

import ipaddress
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

UNKNOWN_BUCKET = "unknown"  # 接続元が読めない人たちをまとめて数える名前（制限の対象外にはしない）


def parse_ip(text: str) -> Address | None:
    """IP の文字を読む。ポート付き（1.2.3.4:80、[::1]:80）と括弧付き（[::1]）も受ける。"""
    text = text.strip()
    candidates = [text]
    if text.startswith("["):
        candidates.append(text[1:].split("]", 1)[0])
    elif text.count(":") == 1:
        candidates.append(text.split(":", 1)[0])
    for candidate in candidates:
        try:
            return ipaddress.ip_address(candidate)
        except ValueError:
            continue
    return None


def _trusted_networks() -> list[Network]:
    networks: list[Network] = []
    for item in settings.HAKONIWA_TRUSTED_PROXIES:
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.error("HAKONIWA_TRUSTED_PROXIES の %r は IP / CIDR として読めません", item)
    return networks


def _is_trusted(ip: Address, networks: list[Network]) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in networks)


def normalize(ip: Address) -> str:
    """回数制限の単位の文字にする（IPv6 は /64、IPv4 を写した IPv6 は IPv4）。"""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network((ip, 64), strict=False).network_address)
    return str(ip)


def client_ip(request) -> str:
    """利用者の IP（回数制限の単位にそろえた文字）。読めない・空のときは空文字。"""
    header = settings.HAKONIWA_CLIENT_IP_HEADER
    peer_text = (request.META.get("REMOTE_ADDR") or "").strip()
    peer = parse_ip(peer_text) if peer_text else None
    if header == "REMOTE_ADDR":
        return normalize(peer) if peer else ""

    networks = _trusted_networks()
    # 空の REMOTE_ADDR は、gunicorn が UNIX ソケットで受けたとき（同じ機械の中の相手だけがつなげる）
    trusted_peer = peer_text == "" or (peer is not None and _is_trusted(peer, networks))
    if not trusted_peer:
        logger.warning(
            "接続元の見出し %s は、信頼する中継元（HAKONIWA_TRUSTED_PROXIES）以外から来たので"
            "使いません",
            header,
        )
        return normalize(peer) if peer else ""

    parts = [p.strip() for p in (request.META.get(header) or "").split(",") if p.strip()]
    for text in reversed(parts):
        ip = parse_ip(text)
        if ip is None:  # 読めない値は、それより左を信じる理由がないので、そこで止める
            return ""
        if not _is_trusted(ip, networks):
            return normalize(ip)
    if parts:  # 全部が中継元なら、いちばん左（中継元どうしの通信）
        ip = parse_ip(parts[0])
        return normalize(ip) if ip else ""
    return ""
