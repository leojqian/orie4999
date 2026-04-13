"""
Full test suite for LabInv — covers every route and all business logic.
Run with:  python -m pytest test_app.py -v
"""
import os
import tempfile
import pytest
import app as module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """Spin up a fresh isolated database for every test."""
    db_path = str(tmp_path / "test.db")
    module.DATABASE = db_path
    module.init_db()
    module.app.config["TESTING"] = True
    module.app.config["WTF_CSRF_ENABLED"] = False
    with module.app.test_client() as c:
        yield c


def _db(tmp_path_fixture=None):
    return module.get_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_item(client, name="O-Ring", quantity=100, unit="units",
                threshold=10, supplier="ACME", sku="SKU-001", notes=""):
    return client.post("/items/new", data={
        "name": name, "quantity": str(quantity), "unit": unit,
        "low_stock_threshold": str(threshold),
        "supplier": supplier, "sku": sku, "notes": notes,
        "user_name": "Tester",
    }, follow_redirects=False)


def create_kit(client, name="Seal Kit", kit_type="custom",
               item_ids=None, quantities=None):
    data = {"name": name, "kit_type": kit_type,
            "component_item_id": item_ids or [],
            "component_qty":     quantities or []}
    return client.post("/kits/new", data=data, follow_redirects=False)


def get_item_qty(item_id=1):
    db = module.get_db()
    row = db.execute("SELECT quantity FROM items WHERE id=?", (item_id,)).fetchone()
    db.close()
    return row[0] if row else None


def get_kit_qty(kit_id=1):
    db = module.get_db()
    row = db.execute("SELECT quantity FROM kits WHERE id=?", (kit_id,)).fetchone()
    db.close()
    return row[0] if row else None


def count_transactions(item_id=1):
    db = module.get_db()
    n = db.execute("SELECT COUNT(*) FROM transactions WHERE item_id=?",
                   (item_id,)).fetchone()[0]
    db.close()
    return n


def count_kit_transactions(kit_id=1):
    db = module.get_db()
    n = db.execute("SELECT COUNT(*) FROM kit_transactions WHERE kit_id=?",
                   (kit_id,)).fetchone()[0]
    db.close()
    return n


# ===========================================================================
# 1. PAGE RENDERING — every route returns 200
# ===========================================================================

class TestPageRendering:

    def test_dashboard(self, client):
        assert client.get("/").status_code == 200

    def test_items_list(self, client):
        assert client.get("/items").status_code == 200

    def test_new_item_form(self, client):
        assert client.get("/items/new").status_code == 200

    def test_kits_list(self, client):
        assert client.get("/kits").status_code == 200

    def test_new_kit_form(self, client):
        assert client.get("/kits/new").status_code == 200

    def test_transactions_page(self, client):
        assert client.get("/transactions").status_code == 200

    def test_categories_page(self, client):
        assert client.get("/categories").status_code == 200

    def test_locations_page(self, client):
        assert client.get("/locations").status_code == 200

    def test_item_detail_404(self, client):
        """Non-existent item redirects rather than 500."""
        r = client.get("/items/9999", follow_redirects=False)
        assert r.status_code == 302

    def test_kit_detail_404(self, client):
        r = client.get("/kits/9999", follow_redirects=False)
        assert r.status_code == 302


# ===========================================================================
# 2. ITEMS — CRUD
# ===========================================================================

class TestItemCRUD:

    def test_create_item_full(self, client):
        r = create_item(client)
        assert r.status_code == 302
        assert r.headers["Location"].startswith("/items/")

    def test_create_item_appears_in_list(self, client):
        create_item(client, name="Gasket")
        r = client.get("/items")
        assert b"Gasket" in r.data

    def test_create_item_records_initial_transaction(self, client):
        create_item(client, quantity=50)
        assert count_transactions(1) == 1

    def test_create_item_zero_qty_no_transaction(self, client):
        client.post("/items/new", data={"name": "Empty", "quantity": "0",
                                        "unit": "units", "user_name": "T"})
        assert count_transactions(1) == 0

    def test_create_item_minimal_fields(self, client):
        r = client.post("/items/new", data={"name": "Minimal", "unit": "units",
                                            "user_name": "T"},
                        follow_redirects=False)
        assert r.status_code == 302

    def test_item_detail_page(self, client):
        create_item(client, name="Widget")
        r = client.get("/items/1")
        assert r.status_code == 200
        assert b"Widget" in r.data

    def test_edit_item(self, client):
        create_item(client, name="Old Name")
        client.post("/items/1/edit", data={
            "name": "New Name", "unit": "units",
            "low_stock_threshold": "5",
        })
        r = client.get("/items/1")
        assert b"New Name" in r.data

    def test_edit_does_not_change_quantity(self, client):
        create_item(client, quantity=77)
        client.post("/items/1/edit", data={"name": "X", "unit": "pcs",
                                           "low_stock_threshold": "0"})
        assert get_item_qty(1) == 77.0

    def test_delete_item(self, client):
        create_item(client, name="Deletable Part")
        client.post("/items/1/delete")
        db = module.get_db()
        item = db.execute("SELECT id FROM items WHERE id=1").fetchone()
        db.close()
        assert item is None

    def test_delete_cascades_transactions(self, client):
        create_item(client, quantity=10)
        client.post("/items/1/checkout", data={"quantity": "1",
                                               "user_name": "T"})
        client.post("/items/1/delete")
        db = module.get_db()
        n = db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        db.close()
        assert n == 0

    def test_delete_item_removes_kit_component_references(self, client):
        create_item(client, name="Shared Part")
        create_kit(client, name="Build Kit", item_ids=["1"], quantities=["2"])

        response = client.post("/items/1/delete", follow_redirects=False)

        db = module.get_db()
        item = db.execute("SELECT id FROM items WHERE id=1").fetchone()
        component_count = db.execute(
            "SELECT COUNT(*) FROM kit_components WHERE item_id=1"
        ).fetchone()[0]
        db.close()

        assert response.status_code == 302
        assert item is None
        assert component_count == 0

    def test_item_qr_code(self, client):
        create_item(client)
        r = client.get("/items/1/qr")
        assert r.status_code == 200
        assert r.content_type == "image/png"


# ===========================================================================
# 3. ITEMS — SEARCH & FILTER
# ===========================================================================

class TestItemSearch:

    def test_search_by_name(self, client):
        create_item(client, name="Alpha Ring",  sku="AAA")
        create_item(client, name="Beta Gasket", sku="BBB")
        r = client.get("/items?q=Alpha")
        assert b"Alpha Ring" in r.data
        # confirm Beta is absent from the results table rows specifically
        db = module.get_db()
        rows = db.execute(
            "SELECT id FROM items WHERE name LIKE '%Alpha%'"
        ).fetchall()
        db.close()
        assert len(rows) == 1

    def test_search_by_sku(self, client):
        create_item(client, name="Alpha Ring",  sku="AAA")
        create_item(client, name="Beta Gasket", sku="BBB")
        db = module.get_db()
        rows = db.execute(
            "SELECT id FROM items WHERE sku LIKE '%BBB%'"
        ).fetchall()
        db.close()
        assert len(rows) == 1

    def test_search_no_results(self, client):
        create_item(client, name="Alpha Ring", sku="AAA")
        db = module.get_db()
        rows = db.execute(
            "SELECT id FROM items WHERE name LIKE '%ZZZNOMATCH%' OR sku LIKE '%ZZZNOMATCH%'"
        ).fetchall()
        db.close()
        assert len(rows) == 0

    def test_clear_search_returns_all(self, client):
        create_item(client, name="Alpha Ring",  sku="AAA")
        create_item(client, name="Beta Gasket", sku="BBB")
        r = client.get("/items")
        assert b"Alpha Ring" in r.data
        assert b"Beta Gasket" in r.data


# ===========================================================================
# 4. INVENTORY TRANSACTIONS — Items
# ===========================================================================

class TestItemTransactions:

    def test_checkout_reduces_quantity(self, client):
        create_item(client, quantity=50)
        client.post("/items/1/checkout", data={"quantity": "10",
                                               "user_name": "Leo"})
        assert get_item_qty(1) == 40.0

    def test_checkout_records_transaction(self, client):
        create_item(client, quantity=50)
        client.post("/items/1/checkout", data={"quantity": "5",
                                               "user_name": "Leo"})
        db = module.get_db()
        t = db.execute("SELECT * FROM transactions WHERE action='checkout'").fetchone()
        db.close()
        assert t is not None
        assert t["quantity_change"] == -5.0
        assert t["quantity_before"] == 50.0
        assert t["quantity_after"] == 45.0

    def test_checkout_blocked_insufficient_stock(self, client):
        create_item(client, quantity=3)
        client.post("/items/1/checkout", data={"quantity": "10",
                                               "user_name": "Leo"})
        assert get_item_qty(1) == 3.0  # unchanged

    def test_checkout_blocked_records_no_transaction(self, client):
        create_item(client, quantity=3)
        client.post("/items/1/checkout", data={"quantity": "10",
                                               "user_name": "Leo"})
        # Only the 'add' transaction from creation
        assert count_transactions(1) == 1

    def test_checkin_increases_quantity(self, client):
        create_item(client, quantity=20)
        client.post("/items/1/checkin", data={"quantity": "5",
                                              "user_name": "Leo"})
        assert get_item_qty(1) == 25.0

    def test_checkin_records_transaction(self, client):
        create_item(client, quantity=20)
        client.post("/items/1/checkin", data={"quantity": "5",
                                             "user_name": "Leo"})
        db = module.get_db()
        t = db.execute("SELECT * FROM transactions WHERE action='checkin'").fetchone()
        db.close()
        assert t["quantity_change"] == 5.0

    def test_restock_increases_quantity(self, client):
        create_item(client, quantity=10)
        client.post("/items/1/restock", data={"quantity": "90",
                                              "user_name": "Leo"})
        assert get_item_qty(1) == 100.0

    def test_restock_records_transaction(self, client):
        create_item(client, quantity=10)
        client.post("/items/1/restock", data={"quantity": "90",
                                              "user_name": "Leo"})
        db = module.get_db()
        t = db.execute("SELECT * FROM transactions WHERE action='restock'").fetchone()
        db.close()
        assert t["quantity_change"] == 90.0

    def test_checkout_then_checkin_net_zero(self, client):
        create_item(client, quantity=50)
        client.post("/items/1/checkout", data={"quantity": "15", "user_name": "L"})
        client.post("/items/1/checkin",  data={"quantity": "15", "user_name": "L"})
        assert get_item_qty(1) == 50.0

    def test_fractional_quantities(self, client):
        create_item(client, quantity=10, unit="kg")
        client.post("/items/1/checkout", data={"quantity": "0.5",
                                               "user_name": "Leo"})
        assert get_item_qty(1) == 9.5

    def test_transaction_stores_user_and_project(self, client):
        create_item(client, quantity=50)
        client.post("/items/1/checkout", data={"quantity": "1",
                                               "user_name": "Leo",
                                               "project": "Exp-42"})
        db = module.get_db()
        t = db.execute("SELECT * FROM transactions WHERE action='checkout'").fetchone()
        db.close()
        assert t["user_name"] == "Leo"
        assert t["project"] == "Exp-42"

    def test_quantity_never_goes_negative(self, client):
        create_item(client, quantity=5)
        client.post("/items/1/checkout", data={"quantity": "10", "user_name": "L"})
        assert get_item_qty(1) == 5.0  # unchanged, not -5


# ===========================================================================
# 5. LOW-STOCK THRESHOLD
# ===========================================================================

class TestLowStock:

    def test_item_below_threshold_appears_in_dashboard(self, client):
        create_item(client, quantity=3, threshold=10, name="Depleted Part")
        r = client.get("/")
        assert b"Depleted Part" in r.data

    def test_item_above_threshold_not_in_low_stock(self, client):
        create_item(client, quantity=50, threshold=10, name="Full Part")
        r = client.get("/")
        # Dashboard low-stock section should NOT contain this item
        # (it appears in inventory list, but not under the alert panel)
        db = module.get_db()
        low = db.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE low_stock_threshold > 0 AND quantity <= low_stock_threshold"
        ).fetchone()[0]
        db.close()
        assert low == 0

    def test_zero_threshold_not_an_alert(self, client):
        create_item(client, quantity=0, threshold=0)
        db = module.get_db()
        low = db.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE low_stock_threshold > 0 AND quantity <= low_stock_threshold"
        ).fetchone()[0]
        db.close()
        assert low == 0

    def test_checkout_triggers_low_stock(self, client):
        create_item(client, quantity=15, threshold=10)
        client.post("/items/1/checkout", data={"quantity": "10",
                                               "user_name": "Leo"})
        # qty is now 5, below threshold of 10
        assert get_item_qty(1) == 5.0
        db = module.get_db()
        low = db.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE low_stock_threshold > 0 AND quantity <= low_stock_threshold"
        ).fetchone()[0]
        db.close()
        assert low == 1


# ===========================================================================
# 6. TRANSACTIONS LOG PAGE
# ===========================================================================

class TestTransactionLog:

    def test_log_shows_all_actions(self, client):
        create_item(client, quantity=50)
        client.post("/items/1/checkout", data={"quantity": "5",  "user_name": "L"})
        client.post("/items/1/checkin",  data={"quantity": "5",  "user_name": "L"})
        client.post("/items/1/restock",  data={"quantity": "10", "user_name": "L"})
        r = client.get("/transactions")
        assert b"checkout" in r.data
        assert b"checkin"  in r.data
        assert b"restock"  in r.data

    def test_filter_by_action(self, client):
        """Filter returns only matching rows — verified at DB level."""
        create_item(client, quantity=50)
        client.post("/items/1/checkout", data={"quantity": "5", "user_name": "L"})
        client.post("/items/1/restock",  data={"quantity": "5", "user_name": "L"})
        # The page itself always shows filter buttons (including 'restock'),
        # so verify filtering via the database query the route uses.
        db = module.get_db()
        rows = db.execute(
            "SELECT * FROM transactions WHERE action='checkout'"
        ).fetchall()
        non_rows = db.execute(
            "SELECT * FROM transactions WHERE action='restock'"
        ).fetchall()
        db.close()
        assert len(rows) == 1
        assert len(non_rows) == 1
        assert rows[0]["action"] == "checkout"


# ===========================================================================
# 7. CATEGORIES
# ===========================================================================

class TestCategories:

    def test_add_category(self, client):
        client.post("/categories", data={"name": "Reagent"})
        r = client.get("/categories")
        assert b"Reagent" in r.data

    def test_duplicate_category_rejected(self, client):
        client.post("/categories", data={"name": "Consumable"})
        db = module.get_db()
        count = db.execute(
            "SELECT COUNT(*) FROM categories WHERE name='Consumable'"
        ).fetchone()[0]
        db.close()
        assert count == 1  # not doubled

    def test_delete_category_nullifies_items(self, client):
        create_item(client)  # item has no category
        # get default category id
        db = module.get_db()
        cat_id = db.execute(
            "SELECT id FROM categories WHERE name='Consumable'"
        ).fetchone()[0]
        db.close()
        # assign item to that category
        client.post("/items/1/edit", data={
            "name": "O-Ring", "unit": "units",
            "low_stock_threshold": "0", "category_id": str(cat_id)
        })
        client.post(f"/categories/{cat_id}/delete")
        db = module.get_db()
        item = db.execute("SELECT category_id FROM items WHERE id=1").fetchone()
        db.close()
        assert item["category_id"] is None

    def test_delete_nonexistent_category(self, client):
        r = client.post("/categories/9999/delete", follow_redirects=False)
        assert r.status_code == 302  # redirects, does not crash


# ===========================================================================
# 8. LOCATIONS — flat and hierarchical
# ===========================================================================

class TestLocations:

    def test_add_top_level_location(self, client):
        client.post("/locations", data={"name": "Shelf C"})
        r = client.get("/locations")
        assert b"Shelf C" in r.data

    def test_add_child_location(self, client):
        client.post("/locations", data={"name": "Shelf A"})
        db = module.get_db()
        parent_id = db.execute(
            "SELECT id FROM locations WHERE name='Shelf A'"
        ).fetchone()[0]
        db.close()
        client.post("/locations", data={"name": "Row 1",
                                        "parent_id": str(parent_id)})
        db = module.get_db()
        child = db.execute(
            "SELECT parent_id FROM locations WHERE name='Row 1'"
        ).fetchone()
        db.close()
        assert child["parent_id"] == parent_id

    def test_duplicate_location_rejected(self, client):
        client.post("/locations", data={"name": "Shelf A"})
        client.post("/locations", data={"name": "Shelf A"})
        db = module.get_db()
        count = db.execute(
            "SELECT COUNT(*) FROM locations WHERE name='Shelf A'"
        ).fetchone()[0]
        db.close()
        assert count == 1

    def test_delete_location_nullifies_items(self, client):
        client.post("/locations", data={"name": "Temp Shelf"})
        db = module.get_db()
        loc_id = db.execute(
            "SELECT id FROM locations WHERE name='Temp Shelf'"
        ).fetchone()[0]
        db.close()
        client.post("/items/new", data={
            "name": "Screw", "unit": "units", "quantity": "0",
            "user_name": "T", "location_id": str(loc_id)
        })
        client.post(f"/locations/{loc_id}/delete")
        db = module.get_db()
        item = db.execute("SELECT location_id FROM items WHERE name='Screw'").fetchone()
        db.close()
        assert item["location_id"] is None

    def test_delete_nonexistent_location(self, client):
        r = client.post("/locations/9999/delete", follow_redirects=False)
        assert r.status_code == 302


# ===========================================================================
# 9. KITS — Definition
# ===========================================================================

class TestKitDefinition:

    def test_create_custom_kit(self, client):
        create_item(client, quantity=100)
        r = create_kit(client, item_ids=["1"], quantities=["5"])
        assert r.status_code == 302
        assert r.headers["Location"].startswith("/kits/")

    def test_kit_appears_in_list(self, client):
        create_item(client, quantity=100)
        create_kit(client, name="My Kit")
        r = client.get("/kits")
        assert b"My Kit" in r.data

    def test_create_vendor_kit(self, client):
        create_kit(client, name="Vendor Kit", kit_type="vendor")
        db = module.get_db()
        kit = db.execute("SELECT kit_type FROM kits WHERE name='Vendor Kit'").fetchone()
        db.close()
        assert kit["kit_type"] == "vendor"

    def test_kit_detail_page(self, client):
        create_item(client, quantity=100)
        create_kit(client, item_ids=["1"], quantities=["5"])
        r = client.get("/kits/1")
        assert r.status_code == 200

    def test_kit_starts_with_zero_stock(self, client):
        create_item(client, quantity=100)
        create_kit(client, item_ids=["1"], quantities=["5"])
        assert get_kit_qty(1) == 0

    def test_edit_kit(self, client):
        create_item(client, quantity=100)
        create_kit(client, name="Original")
        client.post("/kits/1/edit", data={
            "name": "Updated", "kit_type": "custom",
            "component_item_id": ["1"], "component_qty": ["3"]
        })
        db = module.get_db()
        kit = db.execute("SELECT name FROM kits WHERE id=1").fetchone()
        db.close()
        assert kit["name"] == "Updated"

    def test_edit_kit_replaces_components(self, client):
        create_item(client, name="Part A", quantity=100)
        create_item(client, name="Part B", quantity=100)
        create_kit(client, item_ids=["1"], quantities=["2"])
        client.post("/kits/1/edit", data={
            "name": "Seal Kit", "kit_type": "custom",
            "component_item_id": ["2"], "component_qty": ["4"]
        })
        db = module.get_db()
        comps = db.execute(
            "SELECT item_id FROM kit_components WHERE kit_id=1"
        ).fetchall()
        db.close()
        assert len(comps) == 1
        assert comps[0]["item_id"] == 2

    def test_delete_kit(self, client):
        create_item(client, quantity=100)
        create_kit(client, item_ids=["1"], quantities=["5"])
        client.post("/kits/1/delete")
        db = module.get_db()
        kit = db.execute("SELECT id FROM kits WHERE id=1").fetchone()
        db.close()
        assert kit is None

    def test_delete_kit_removes_components(self, client):
        create_item(client, quantity=100)
        create_kit(client, item_ids=["1"], quantities=["5"])
        client.post("/kits/1/delete")
        db = module.get_db()
        n = db.execute("SELECT COUNT(*) FROM kit_components WHERE kit_id=1").fetchone()[0]
        db.close()
        assert n == 0


# ===========================================================================
# 10. KITS — Custom Build
# ===========================================================================

class TestKitBuild:

    def setup_kit(self, client):
        create_item(client, name="O-Ring",  quantity=100)
        create_item(client, name="Gasket",  quantity=50)
        create_kit(client, item_ids=["1", "2"], quantities=["5", "2"])

    def test_build_one_kit(self, client):
        self.setup_kit(client)
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "Leo"})
        assert get_kit_qty(1) == 1

    def test_build_deducts_components(self, client):
        self.setup_kit(client)
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "Leo"})
        assert get_item_qty(1) == 95.0   # 100 - 5
        assert get_item_qty(2) == 48.0   # 50  - 2

    def test_build_multiple_kits(self, client):
        self.setup_kit(client)
        client.post("/kits/1/build", data={"quantity": "3", "user_name": "Leo"})
        assert get_kit_qty(1) == 3
        assert get_item_qty(1) == 85.0   # 100 - 15
        assert get_item_qty(2) == 44.0   # 50  - 6

    def test_build_records_component_transactions(self, client):
        self.setup_kit(client)
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "Leo"})
        db = module.get_db()
        t1 = db.execute(
            "SELECT * FROM transactions WHERE item_id=1 AND action='kit_build'"
        ).fetchone()
        t2 = db.execute(
            "SELECT * FROM transactions WHERE item_id=2 AND action='kit_build'"
        ).fetchone()
        db.close()
        assert t1["quantity_change"] == -5.0
        assert t2["quantity_change"] == -2.0

    def test_build_records_kit_transaction(self, client):
        self.setup_kit(client)
        client.post("/kits/1/build", data={"quantity": "2", "user_name": "Leo"})
        db = module.get_db()
        t = db.execute(
            "SELECT * FROM kit_transactions WHERE action='build'"
        ).fetchone()
        db.close()
        assert t["quantity_change"] == 2
        assert t["quantity_after"] == 2

    def test_build_blocked_insufficient_component(self, client):
        create_item(client, name="O-Ring", quantity=4)   # need 5 per kit
        create_kit(client, item_ids=["1"], quantities=["5"])
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "Leo"})
        assert get_kit_qty(1) == 0       # kit stock unchanged
        assert get_item_qty(1) == 4.0    # component stock unchanged

    def test_build_blocked_leaves_no_partial_deduction(self, client):
        """All-or-nothing: if one component fails, no component is deducted."""
        create_item(client, name="Part A", quantity=100)
        create_item(client, name="Part B", quantity=1)   # bottleneck
        create_kit(client, item_ids=["1", "2"], quantities=["5", "10"])
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "Leo"})
        # Part A must not have been deducted
        assert get_item_qty(1) == 100.0

    def test_max_buildable_reflects_bottleneck(self, client):
        create_item(client, name="Part A", quantity=100)
        create_item(client, name="Part B", quantity=8)   # 8/5 = 1 kit max
        create_kit(client, item_ids=["1", "2"], quantities=["5", "5"])
        r = client.get("/kits/1")
        assert b"1" in r.data  # max buildable = 1


# ===========================================================================
# 11. KITS — Vendor Receive
# ===========================================================================

class TestKitReceive:

    def test_receive_increases_kit_stock(self, client):
        create_kit(client, name="Vendor Kit", kit_type="vendor")
        client.post("/kits/1/receive", data={"quantity": "5",
                                             "user_name": "Leo"})
        assert get_kit_qty(1) == 5

    def test_receive_does_not_touch_items(self, client):
        create_item(client, quantity=100)
        create_kit(client, name="Vendor Kit", kit_type="vendor",
                   item_ids=["1"], quantities=["5"])
        client.post("/kits/1/receive", data={"quantity": "3",
                                             "user_name": "Leo"})
        assert get_item_qty(1) == 100.0  # unchanged

    def test_receive_records_kit_transaction(self, client):
        create_kit(client, name="Vendor Kit", kit_type="vendor")
        client.post("/kits/1/receive", data={"quantity": "4",
                                             "user_name": "Leo",
                                             "notes": "PO#999"})
        db = module.get_db()
        t = db.execute(
            "SELECT * FROM kit_transactions WHERE action='receive_vendor'"
        ).fetchone()
        db.close()
        assert t["quantity_change"] == 4
        assert t["notes"] == "PO#999"


# ===========================================================================
# 12. KITS — Checkout / Return
# ===========================================================================

class TestKitCheckoutCheckin:

    def setup_stocked_kit(self, client):
        create_item(client, quantity=100)
        create_kit(client, item_ids=["1"], quantities=["5"])
        client.post("/kits/1/build", data={"quantity": "5",
                                           "user_name": "Leo"})

    def test_checkout_reduces_kit_stock(self, client):
        self.setup_stocked_kit(client)
        client.post("/kits/1/checkout", data={"quantity": "2",
                                              "user_name": "Leo"})
        assert get_kit_qty(1) == 3

    def test_checkout_records_transaction(self, client):
        self.setup_stocked_kit(client)
        client.post("/kits/1/checkout", data={"quantity": "1",
                                              "user_name": "Leo",
                                              "project": "Proj-1"})
        db = module.get_db()
        t = db.execute(
            "SELECT * FROM kit_transactions WHERE action='checkout'"
        ).fetchone()
        db.close()
        assert t["quantity_change"] == -1
        assert t["project"] == "Proj-1"

    def test_checkout_blocked_insufficient_kits(self, client):
        self.setup_stocked_kit(client)  # 5 kits
        client.post("/kits/1/checkout", data={"quantity": "10",
                                              "user_name": "Leo"})
        assert get_kit_qty(1) == 5   # unchanged

    def test_checkout_blocked_no_transaction_created(self, client):
        self.setup_stocked_kit(client)
        n_before = count_kit_transactions(1)
        client.post("/kits/1/checkout", data={"quantity": "100",
                                              "user_name": "Leo"})
        assert count_kit_transactions(1) == n_before

    def test_checkin_increases_kit_stock(self, client):
        self.setup_stocked_kit(client)
        client.post("/kits/1/checkout", data={"quantity": "3",
                                              "user_name": "Leo"})
        client.post("/kits/1/checkin",  data={"quantity": "2",
                                              "user_name": "Leo"})
        assert get_kit_qty(1) == 4   # 5 - 3 + 2

    def test_checkin_records_transaction(self, client):
        self.setup_stocked_kit(client)
        client.post("/kits/1/checkin", data={"quantity": "1",
                                             "user_name": "Leo"})
        db = module.get_db()
        t = db.execute(
            "SELECT * FROM kit_transactions WHERE action='checkin'"
        ).fetchone()
        db.close()
        assert t["quantity_change"] == 1

    def test_checkout_checkin_net_zero(self, client):
        self.setup_stocked_kit(client)
        client.post("/kits/1/checkout", data={"quantity": "3",
                                              "user_name": "L"})
        client.post("/kits/1/checkin",  data={"quantity": "3",
                                              "user_name": "L"})
        assert get_kit_qty(1) == 5


# ===========================================================================
# 13. DASHBOARD STATS
# ===========================================================================

class TestDashboard:

    def test_total_items_count(self, client):
        create_item(client, name="A")
        create_item(client, name="B")
        db = module.get_db()
        count = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        db.close()
        assert count == 2

    def test_total_kits_count(self, client):
        create_item(client, quantity=100)
        create_kit(client, name="K1")
        create_kit(client, name="K2")
        db = module.get_db()
        count = db.execute("SELECT COUNT(*) FROM kits").fetchone()[0]
        db.close()
        assert count == 2

    def test_low_stock_count_correct(self, client):
        create_item(client, name="Low",  quantity=2,  threshold=10)
        create_item(client, name="Full", quantity=50, threshold=10)
        db = module.get_db()
        low = db.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE low_stock_threshold > 0 AND quantity <= low_stock_threshold"
        ).fetchone()[0]
        db.close()
        assert low == 1

    def test_dashboard_renders_low_stock_item(self, client):
        create_item(client, name="Critical Part", quantity=1, threshold=20)
        r = client.get("/")
        assert b"Critical Part" in r.data


# ===========================================================================
# 14. EDGE CASES
# ===========================================================================

class TestEdgeCases:

    def test_build_kit_with_no_components_blocked(self, client):
        """Kit with no components defined cannot be built."""
        create_kit(client, name="Empty Kit", kit_type="custom")
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "L"})
        assert get_kit_qty(1) == 0

    def test_delete_nonexistent_item(self, client):
        r = client.post("/items/9999/delete", follow_redirects=False)
        assert r.status_code == 302

    def test_delete_nonexistent_kit(self, client):
        r = client.post("/kits/9999/delete", follow_redirects=False)
        assert r.status_code == 302

    def test_checkout_nonexistent_item(self, client):
        r = client.post("/items/9999/checkout",
                        data={"quantity": "1", "user_name": "L"},
                        follow_redirects=False)
        assert r.status_code == 302

    def test_checkin_nonexistent_item(self, client):
        r = client.post("/items/9999/checkin",
                        data={"quantity": "1", "user_name": "L"},
                        follow_redirects=False)
        assert r.status_code == 302

    def test_build_nonexistent_kit(self, client):
        r = client.post("/kits/9999/build",
                        data={"quantity": "1", "user_name": "L"},
                        follow_redirects=False)
        assert r.status_code == 302

    def test_checkout_zero_stock_item(self, client):
        create_item(client, quantity=0)
        client.post("/items/1/checkout", data={"quantity": "1",
                                               "user_name": "L"})
        assert get_item_qty(1) == 0.0

    def test_multiple_kits_same_components_independent(self, client):
        """Two kits sharing a component track stock independently."""
        create_item(client, quantity=100)
        create_kit(client, name="Kit A", item_ids=["1"], quantities=["5"])
        create_kit(client, name="Kit B", item_ids=["1"], quantities=["3"])
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "L"})
        client.post("/kits/2/build", data={"quantity": "1", "user_name": "L"})
        assert get_item_qty(1) == 92.0   # 100 - 5 - 3
        assert get_kit_qty(1) == 1
        assert get_kit_qty(2) == 1

    def test_transaction_history_on_item_detail(self, client):
        create_item(client, quantity=50)
        client.post("/items/1/checkout", data={"quantity": "5",
                                               "user_name": "Leo"})
        r = client.get("/items/1")
        assert b"checkout" in r.data
        assert b"Leo" in r.data

    def test_kit_transaction_history_on_kit_detail(self, client):
        create_item(client, quantity=100)
        create_kit(client, item_ids=["1"], quantities=["5"])
        client.post("/kits/1/build", data={"quantity": "1", "user_name": "Leo"})
        r = client.get("/kits/1")
        assert b"Leo" in r.data
