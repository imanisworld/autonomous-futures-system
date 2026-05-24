"""CLI entrypoint for read-only notification smoke tests."""

from notifications.discord_notifier import main


if __name__ == "__main__":
    raise SystemExit(main())
