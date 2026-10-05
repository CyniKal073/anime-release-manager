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
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.poolmanager import PoolManager


class IPv6FirstHTTPSConnection(HTTPSConnection):
    """只改地址选择顺序，证书校验与 SNI 仍然使用原主机名。"""

    def _new_conn(self):  # type: ignore[override]
        try:
            infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            raise OSError(f"无法解析主机 {self.host}：{exc}") from exc

        # IPv6 排前面；IPv4 保留作为回落
        infos = sorted(infos, key=lambda info: 0 if info[0] == socket.AF_INET6 else 1)

        last_error: Optional[Exception] = None
        for family, socktype, proto, _canonname, sockaddr in infos:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                for option in self.socket_options or []:
                    sock.setsockopt(*option)
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
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
