import asyncio
import getpass
import logging

from butterflymx import ButterflyMXClient


async def main():
    email = input("ButterflyMX email: ")
    password = getpass.getpass("ButterflyMX password: ")

    print(f"Using credentials for: {email}")

    client = ButterflyMXClient(email, password, token_file="tokens.json")
    if await client.login():
        tenants = await client.get_tenants()
        print(f"\nFound {len(tenants)} Tenants:")

        all_doors = []

        for t in tenants:
            print(f"- {t.name} (ID: {t.id})")

            doors = await t.get_doors()
            print(f"  Found {len(doors)} doors:")
            for d in doors:
                status = "Online" if d.online else "Offline"
                print(f"  [{len(all_doors)}] {d.name} ({d.building_name}) - {status}")
                all_doors.append((d, t.id))

            # Fetch & Print Messages
            msgs = await t.get_messages()
            print(f"  Found {len(msgs)} messages (showing last 3):")
            for m in msgs[:3]:
                 print(f"    - [{m.created_at}] From: {m.visitor_name} (Source: {m.source})")
                 print(f"      Body: {m.body}")
                 print(f"      Image: {m.image_url}")

            # Fetch & Print Calls
            calls = await t.get_calls()
            print(f"  Found {len(calls)} calls (showing last 3):")
            for c in calls[:3]:
                 print(f"    - [{c.logged_at}] {c.device} ({c.type}): {c.status}")
                 print(f"      Image: {c.image_url}")

            # Fetch & Print Access Logs
            access_logs = await t.get_access_logs()
            print(f"  Found {len(access_logs)} access logs (showing last 3):")
            for a in access_logs[:3]:
                 print(f"    - [{a.logged_at}] {a.type} via {a.method} at {a.door_name}")
                 print(f"      Device: {a.device_name}")
                 print(f"      Image: {a.image_url}")


        if all_doors:
            print("\nOptions:")
            print("Enter the number of the door to open, or 'q' to quit.")
            choice = input("Choice: ")
            if choice.lower() != 'q':
                try:
                    idx = int(choice)
                    if 0 <= idx < len(all_doors):
                        door, tenant_id = all_doors[idx]
                        await door.open()
                    else:
                        print("Invalid selection.")
                except ValueError:
                    print("Invalid input.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
