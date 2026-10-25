"""The PostgreSQL listener: one session per client, backed by one pgAdmin Query Tool."""
from .server import Server

__all__ = ["Server"]
