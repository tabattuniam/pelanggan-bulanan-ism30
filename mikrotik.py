"""Mikrotik client — multi-server hotspot user management."""
from __future__ import annotations
import asyncio
from librouteros import connect
from librouteros.login import plain, token


class MikrotikClient:
    def __init__(self, servers: list[dict]):
        self.servers = servers

    def _connect(self, server: dict):
        kw = dict(host=server["host"], port=int(server["port"]),
                  username=server["username"], password=server["password"], timeout=15)
        try:
            return connect(**kw, login_method=plain)
        except Exception:
            return connect(**kw, login_method=token)

    def _create_on_server(self, server: dict, username: str, password: str, profile: str):
        api = self._connect(server)
        try:
            existing = [r for r in api.path("/ip/hotspot/user") if r.get("name") == username]
            if existing:
                return False, "username sudah ada"
            api.path("/ip/hotspot/user").add(name=username, password=password, profile=profile)
            return True, "ok"
        finally:
            api.close()

    def _create_all(self, username: str, password: str, profile: str) -> dict:
        results = {}
        for srv in self.servers:
            try:
                ok, msg = self._create_on_server(srv, username, password, profile)
                results[srv["name"]] = {"ok": ok, "msg": msg}
            except Exception as e:
                results[srv["name"]] = {"ok": False, "msg": str(e)}
        return results

    def _get_profiles(self) -> list[str]:
        for srv in self.servers:
            try:
                api = self._connect(srv)
                rows = list(api.path("/ip/hotspot/user/profile"))
                api.close()
                return [str(r.get("name", "")) for r in rows if r.get("name")]
            except Exception:
                continue
        return []

    async def create_hotspot_user(self, username: str, password: str, profile: str) -> dict:
        return await asyncio.to_thread(self._create_all, username, password, profile)

    async def get_profiles(self) -> list[str]:
        return await asyncio.to_thread(self._get_profiles)
