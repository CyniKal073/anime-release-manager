"""连接层：IPv6 优先的 HTTPS 连接。

背景（实测）：某些网络环境下 ``nekobt.to`` 的 A 记录会被 DNS 污染成
45.67.223.32，而该地址返回的证书是签给它自己的，于是报
``CertificateError: hostname 'nekobt.to' doesn't match '45.67.223.32'``；
同一时刻 AAAA 记录解析到 Cloudflare 是正常的。

Python 的 socket 会按 ``getaddrinfo`` 返回顺序取第一个地址，正好是坏的那个，
所以稳定失败；curl 优先 IPv6 因此不受影响。

这里的做法是：**主机名仍然用于 SNI 和证书校验（不降低安全性）**，只把 TCP
连接的目标地址改成 IPv6 优先，连不上再回落到 IPv4。
"""

from __future__ import annotations

import socket
import threading
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.poolmanager import PoolManager

#: 记住每个主机实际可用的地址族，避免每次请求都先等一遍不可用的那个。
#: 实测有些网络下 IPv6 是通的（nekobt.to），有些反过来，所以不能写死。
_PREFERRED_FAMILY: dict = {}
_FAMILY_LOCK = threading.Lock()

#: 单个地址的连接超时上限（秒）。不做限制的话，一个不通的地址会把整体拖到
#: 请求超时（实测出现过单次桥接 12 秒的情况）。
CONNECT_TIMEOUT_CAP = 5.0


def _order(infos, host: str):
    with _FAMILY_LOCK:
        preferred = _PREFERRED_FAMILY.get(host, socket.AF_INET6)
    return sorted(infos, key=lambda info: 0 if info[0] == preferred else 1)


def _remember(host: str, family: int) -> None:
    with _FAMILY_LOCK:
        _PREFERRED_FAMILY[host] = family


class IPv6FirstHTTPSConnection(HTTPSConnection):
    """只改地址选择顺序，证书校验与 SNI 仍然使用原主机名。"""

    def _new_conn(self):  # type: ignore[override]
        try:
            infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            raise OSError(f"无法解析主机 {self.host}：{exc}") from exc

        # 默认 IPv6 优先；连通过一次之后按该主机实际可用的地址族排序
        infos = _order(infos, self.host)

        try:
            connect_timeout = min(float(self.timeout), CONNECT_TIMEOUT_CAP)
        except (TypeError, ValueError):
            connect_timeout = CONNECT_TIMEOUT_CAP

        last_error: Optional[Exception] = None
        for family, socktype, proto, _canonname, sockaddr in infos:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                for option in self.socket_options or []:
                    sock.setsockopt(*option)
                sock.settimeout(connect_timeout)
                sock.connect(sockaddr)
                _remember(self.host, family)
                sock.settimeout(self.timeout)  # 连上后恢复请求本身的超时
                return sock
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        raise last_error or OSError(f"无法连接到 {self.host}:{self.port}")


class IPv6FirstHTTPSConnectionPool(HTTPSConnectionPool):
    """HTTPS 连接池，底层换成 IPv6 优先的连接实现。"""

    ConnectionCls = IPv6FirstHTTPSConnection


class IPv6FirstAdapter(HTTPAdapter):
    """让 requests 的 HTTPS 连接走 IPv6 优先。

    注意：urllib3 1.26 的 ``PoolManager`` 不接受 ``connection_class`` 参数
    （会一路传到 PoolKey 里报 TypeError），所以这里改成替换该实例的
    https 连接池实现，不触碰全局。
    """

    def init_poolmanager(  # type: ignore[override]
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            "https": IPv6FirstHTTPSConnectionPool,
        }


def build_session() -> requests.Session:
    """构造默认会话：HTTPS 走 IPv6 优先，HTTP 保持原样。"""
    session = requests.Session()
    session.mount("https://", IPv6FirstAdapter())
    return session
