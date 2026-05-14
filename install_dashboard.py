"""
Beezy Agents — Dashboard installer
Run from ~/workspace: python3 install_dashboard.py
"""
import shutil
import os

def patch(filepath, old, new, label):
    with open(filepath, 'r') as f:
        content = f.read()
    if old not in content:
        print(f"  SKIP {label} — pattern not found in {filepath}")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  ✓ {label}")
    return True

print("=" * 60)
print("Dashboard Installer")
print("=" * 60)

# Step 1: Copy module
print("\n[1] Installing dashboard module...")
if os.path.exists("dashboard.py"):
    shutil.copy2("dashboard.py", "app/dashboard.py")
    print("  ✓ dashboard.py → app/dashboard.py")
else:
    print("  ✗ dashboard.py not found!")
    exit(1)

# Step 2: Add router to FastAPI app
print("\n[2] Adding dashboard route to app/main.py...")

# Add import after FastAPI import
patch(
    "app/main.py",
    "from fastapi import FastAPI",
    "from fastapi import FastAPI\nfrom app.dashboard import router as dashboard_router",
    "dashboard import"
)

# Include router after app creation — find the lifespan/app creation
# Try to add after the app = FastAPI(...) line
with open("app/main.py", 'r') as f:
    content = f.read()

if "dashboard_router" not in content or "app.include_router" not in content:
    # Find where the app is created and add include_router after
    if "async def healthz" in content:
        patch(
            "app/main.py",
            "async def healthz():",
            """async def _setup_routes():
    pass

app.include_router(dashboard_router)


async def healthz():""",
            "dashboard router included"
        )
    else:
        print("  SKIP — couldn't find insertion point for router. Add manually:")
        print("    app.include_router(dashboard_router)")
else:
    print("  SKIP — dashboard_router already included")

print("\n" + "=" * 60)
print("Dashboard installed. Verify:")
print('  python3 -c "from app.dashboard import router; print(\'OK\')"')
print('  grep -n "dashboard" app/main.py | head -5')
print("=" * 60)
print()
print("Dashboard URL: https://<your-replit-domain>/dashboard")
print("Auto-refreshes every 5 minutes.")
print()
print("Shows:")
print("  - Revenue pacing gauge (MTD vs $150K)")
print("  - Today's campaigns with status")
print("  - This week's performance by audience")
print("  - Next 7 days calendar")
print("  - Flow health summary")
print("  - Recent validator blocks")
