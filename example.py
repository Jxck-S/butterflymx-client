import asyncio
import getpass
import logging

from butterflymx import ButterflyMXClient, ButterflyMXError


async def main():
    email = input("ButterflyMX email: ")
    password = getpass.getpass("ButterflyMX password: ")

    async with ButterflyMXClient(email, password, token_file="tokens.json") as client:
        try:
            tenants = await client.get_tenants()
        except ButterflyMXError as e:
            print(f"Failed: {e}")
            return

        print(f"\nFound {len(tenants)} tenants:")
        all_doors = []

        for t in tenants:
            print(f"- {t.name} (ID: {t.id})")

            # Doors, messages, calls and access logs in one request
            overview = await t.get_overview()

            print(f"  {len(overview.doors)} doors:")
            for d in overview.doors:
                status = "Online" if d.online else "Offline"
                print(f"  [{len(all_doors)}] {d.name} ({d.building_name}) - {status}")
                all_doors.append(d)

            print(f"  {len(overview.messages)} messages (showing last 3):")
            for m in overview.messages[:3]:
                print(f"    - [{m.created_at}] From: {m.visitor_name} (Source: {m.source})")
                print(f"      Body: {m.body}")
                print(f"      Image: {m.image_url}")

            print(f"  {len(overview.calls)} calls (showing last 3):")
            for c in overview.calls[:3]:
                print(f"    - [{c.logged_at}] {c.device} ({c.type}): {c.status}")
                print(f"      Image: {c.image_url}")

            print(f"  {len(overview.access_logs)} access logs (showing last 3):")
            for a in overview.access_logs[:3]:
                print(f"    - [{a.logged_at}] {a.type} via {a.method} at {a.door_name}")
                print(f"      Device: {a.device_name}")
                print(f"      Image: {a.image_url}")

        if not all_doors:
            return
        choice = input("\nEnter the number of a door to open, or 'q' to quit: ")
        if choice.lower() == "q":
            return
        try:
            door = all_doors[int(choice)]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return
        try:
            await door.open()
            print(f"Opened {door.name}.")
        except ButterflyMXError as e:
            print(f"Failed to open {door.name}: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
