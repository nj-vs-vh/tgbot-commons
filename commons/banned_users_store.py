from redis import Redis
from typing import Set


class BannedUsersStore:
    def __init__(self, bot_prefix: str, redis: Redis):
        self.redis = redis
        self.prefix = bot_prefix
        self._update_stored_banned_users()

    @property
    def banned_users_key(self) -> str:
        return f"{self.prefix}-banned"

    def _update_stored_banned_users(self):
        banned_users = {int(d) for d in self.redis.lrange(self.banned_users_key, 0, -1)}
        self._banned_users = banned_users

    def ban_user(self, user_id: int):
        try:
            # TODO: synchronization here, race condition is possible
            self.redis.rpush(self.banned_users_key, str(user_id))
            self._banned_users.add(user_id)
        except:
            self._update_stored_banned_users()

    def is_banned(self, user_id: int) -> bool:
        return user_id in self._banned_users
