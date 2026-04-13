import io
import sqlite3
from datetime import datetime

import qrcode
from flask import (Flask, flash, redirect, render_template, request,
                   send_file, url_for)

app = Flask(__name__)
app.secret_key = "lab-inventory-mvp-key"
DATABASE = "inventory.db"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS locations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES locations(id)
        );

        CREATE TABLE IF NOT EXISTS items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT    NOT NULL,
            category_id         INTEGER REFERENCES categories(id),
            quantity            REAL    NOT NULL DEFAULT 0,
            unit                TEXT    NOT NULL DEFAULT 'units',
            location_id         INTEGER REFERENCES locations(id),
            low_stock_threshold REAL    DEFAULT 0,
            supplier            TEXT,
            sku                 TEXT,
            notes               TEXT,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id         INTEGER NOT NULL REFERENCES items(id),
            action          TEXT    NOT NULL,
            quantity_change REAL    NOT NULL,
            quantity_before REAL    NOT NULL,
            quantity_after  REAL    NOT NULL,
            user_name       TEXT    NOT NULL,
            project         TEXT,
            notes           TEXT,
            timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS kits (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            kit_type    TEXT    NOT NULL DEFAULT 'custom',
            supplier    TEXT,
            sku         TEXT,
            quantity    INTEGER NOT NULL DEFAULT 0,
            location_id INTEGER REFERENCES locations(id),
            notes       TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS kit_components (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            kit_id           INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
            item_id          INTEGER NOT NULL REFERENCES items(id),
            quantity_per_kit REAL    NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS kit_transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            kit_id          INTEGER NOT NULL REFERENCES kits(id),
            action          TEXT    NOT NULL,
            quantity_change INTEGER NOT NULL,
            quantity_before INTEGER NOT NULL,
            quantity_after  INTEGER NOT NULL,
            user_name       TEXT    NOT NULL,
            project         TEXT,
            notes           TEXT,
            timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Default seed data
    for cat in ("Consumable", "Component", "Tool", "Material"):
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat,))
    for loc in ("Shelf A", "Shelf B", "Cabinet 1", "Workbench", "Storage Room"):
        conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (loc,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    db = get_db()
    low_stock = db.execute("""
        SELECT i.*, c.name AS category_name, l.name AS location_name
        FROM items i
        LEFT JOIN categories c ON i.category_id = c.id
        LEFT JOIN locations  l ON i.location_id  = l.id
        WHERE i.low_stock_threshold > 0 AND i.quantity <= i.low_stock_threshold
        ORDER BY (i.quantity - i.low_stock_threshold) ASC
    """).fetchall()

    recent = db.execute("""
        SELECT t.*, i.name AS item_name
        FROM transactions t
        JOIN items i ON t.item_id = i.id
        ORDER BY t.timestamp DESC
        LIMIT 12
    """).fetchall()

    stats = {
        "total_items":      db.execute("SELECT COUNT(*) FROM items").fetchone()[0],
        "total_categories": db.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
        "total_kits":       db.execute("SELECT COUNT(*) FROM kits").fetchone()[0],
        "low_stock_count":  len(low_stock),
        "txn_today":        db.execute(
            "SELECT COUNT(*) FROM transactions WHERE DATE(timestamp) = DATE('now')"
        ).fetchone()[0],
    }
    db.close()
    return render_template("index.html", low_stock=low_stock, recent=recent, stats=stats)


# ---------------------------------------------------------------------------
# Items — list
# ---------------------------------------------------------------------------

@app.route("/items")
def items():
    db = get_db()
    q        = request.args.get("q", "")
    category = request.args.get("category", "")
    location = request.args.get("location", "")

    sql    = """
        SELECT i.*, c.name AS category_name, l.name AS location_name
        FROM items i
        LEFT JOIN categories c ON i.category_id = c.id
        LEFT JOIN locations  l ON i.location_id  = l.id
        WHERE 1=1
    """
    params = []
    if q:
        sql += " AND (i.name LIKE ? OR i.sku LIKE ? OR i.notes LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if category:
        sql += " AND c.name = ?"
        params.append(category)
    if location:
        sql += " AND l.name = ?"
        params.append(location)
    sql += " ORDER BY i.name"

    item_list  = db.execute(sql, params).fetchall()
    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    locations  = db.execute("SELECT * FROM locations  ORDER BY name").fetchall()
    db.close()
    return render_template("items.html", items=item_list, categories=categories,
                           locations=locations, q=q,
                           selected_category=category, selected_location=location)


# ---------------------------------------------------------------------------
# Items — add
# ---------------------------------------------------------------------------

@app.route("/items/new", methods=["GET", "POST"])
def new_item():
    db = get_db()
    if request.method == "POST":
        name       = request.form["name"].strip()
        cat_id     = request.form.get("category_id") or None
        quantity   = float(request.form.get("quantity", 0))
        unit       = request.form.get("unit", "units").strip() or "units"
        loc_id     = request.form.get("location_id") or None
        threshold  = float(request.form.get("low_stock_threshold", 0))
        supplier   = request.form.get("supplier", "").strip()
        sku        = request.form.get("sku", "").strip()
        notes      = request.form.get("notes", "").strip()
        user_name  = request.form.get("user_name", "System").strip() or "System"

        cur = db.execute("""
            INSERT INTO items (name, category_id, quantity, unit, location_id,
                               low_stock_threshold, supplier, sku, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, cat_id, quantity, unit, loc_id, threshold, supplier, sku, notes))
        item_id = cur.lastrowid

        if quantity > 0:
            db.execute("""
                INSERT INTO transactions
                    (item_id, action, quantity_change, quantity_before, quantity_after, user_name, notes)
                VALUES (?, 'add', ?, 0, ?, ?, 'Initial stock')
            """, (item_id, quantity, quantity, user_name))

        db.commit()
        db.close()
        flash(f'Item "{name}" added.', "success")
        return redirect(url_for("item_detail", item_id=item_id))

    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    locations  = db.execute("SELECT * FROM locations  ORDER BY name").fetchall()
    db.close()
    return render_template("item_form.html", item=None,
                           categories=categories, locations=locations)


# ---------------------------------------------------------------------------
# Items — detail
# ---------------------------------------------------------------------------

@app.route("/items/<int:item_id>")
def item_detail(item_id):
    db = get_db()
    item = db.execute("""
        SELECT i.*, c.name AS category_name, l.name AS location_name
        FROM items i
        LEFT JOIN categories c ON i.category_id = c.id
        LEFT JOIN locations  l ON i.location_id  = l.id
        WHERE i.id = ?
    """, (item_id,)).fetchone()

    if not item:
        flash("Item not found.", "danger")
        return redirect(url_for("items"))

    history = db.execute("""
        SELECT * FROM transactions WHERE item_id = ?
        ORDER BY timestamp DESC LIMIT 30
    """, (item_id,)).fetchall()

    kit_usage = db.execute("""
        SELECT k.id, k.name FROM kit_components kc
        JOIN kits k ON k.id = kc.kit_id
        WHERE kc.item_id = ?
        ORDER BY k.name
    """, (item_id,)).fetchall()

    db.close()
    return render_template("item_detail.html", item=item, history=history,
                           kit_usage=kit_usage)


# ---------------------------------------------------------------------------
# Items — edit
# ---------------------------------------------------------------------------

@app.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
def edit_item(item_id):
    db = get_db()
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        flash("Item not found.", "danger")
        return redirect(url_for("items"))

    if request.method == "POST":
        name      = request.form["name"].strip()
        cat_id    = request.form.get("category_id") or None
        unit      = request.form.get("unit", "units").strip() or "units"
        loc_id    = request.form.get("location_id") or None
        threshold = float(request.form.get("low_stock_threshold", 0))
        supplier  = request.form.get("supplier", "").strip()
        sku       = request.form.get("sku", "").strip()
        notes     = request.form.get("notes", "").strip()

        db.execute("""
            UPDATE items SET name=?, category_id=?, unit=?, location_id=?,
                             low_stock_threshold=?, supplier=?, sku=?, notes=?,
                             updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (name, cat_id, unit, loc_id, threshold, supplier, sku, notes, item_id))
        db.commit()
        db.close()
        flash(f'Item "{name}" updated.', "success")
        return redirect(url_for("item_detail", item_id=item_id))

    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    locations  = db.execute("SELECT * FROM locations  ORDER BY name").fetchall()
    db.close()
    return render_template("item_form.html", item=item,
                           categories=categories, locations=locations)


# ---------------------------------------------------------------------------
# Items — delete
# ---------------------------------------------------------------------------

@app.route("/items/<int:item_id>/delete", methods=["POST"])
def delete_item(item_id):
    db = get_db()
    item = db.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
    if item:
        force = request.form.get("force") == "1"
        if not force:
            kit_usage = db.execute("""
                SELECT k.name FROM kit_components kc
                JOIN kits k ON k.id = kc.kit_id
                WHERE kc.item_id = ?
            """, (item_id,)).fetchall()
            if kit_usage:
                kit_names = ", ".join(r["name"] for r in kit_usage)
                flash(
                    f'"{item["name"]}" is a component in: {kit_names}. '
                    f'Deleting it will remove it from those kits. Confirm below.',
                    "warning"
                )
                db.close()
                return redirect(url_for("item_detail", item_id=item_id))

        db.execute("DELETE FROM transactions WHERE item_id = ?", (item_id,))
        db.execute("DELETE FROM kit_components WHERE item_id = ?", (item_id,))
        db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        db.commit()
        flash(f'Item "{item["name"]}" deleted.', "success")
    db.close()
    return redirect(url_for("items"))


# ---------------------------------------------------------------------------
# Checkout / Checkin / Restock
# ---------------------------------------------------------------------------

def _apply_transaction(db, item_id, action, delta, user_name, project, notes):
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return False, "Item not found."
    qty_before = item["quantity"]
    qty_after  = qty_before + delta
    if qty_after < 0:
        return False, f"Not enough stock (available: {qty_before} {item['unit']})."
    db.execute(
        "UPDATE items SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (qty_after, item_id),
    )
    db.execute("""
        INSERT INTO transactions
            (item_id, action, quantity_change, quantity_before, quantity_after, user_name, project, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_id, action, delta, qty_before, qty_after, user_name, project, notes))
    db.commit()
    return True, item


@app.route("/items/<int:item_id>/checkout", methods=["POST"])
def checkout(item_id):
    qty       = float(request.form.get("quantity", 1))
    user_name = request.form.get("user_name", "Unknown").strip() or "Unknown"
    project   = request.form.get("project", "").strip()
    notes     = request.form.get("notes", "").strip()
    db        = get_db()
    ok, result = _apply_transaction(db, item_id, "checkout", -qty, user_name, project, notes)
    if ok:
        flash(f'Checked out {qty} {result["unit"]} of "{result["name"]}".', "success")
    else:
        flash(result, "danger")
    db.close()
    return redirect(url_for("item_detail", item_id=item_id))


@app.route("/items/<int:item_id>/checkin", methods=["POST"])
def checkin(item_id):
    qty       = float(request.form.get("quantity", 1))
    user_name = request.form.get("user_name", "Unknown").strip() or "Unknown"
    project   = request.form.get("project", "").strip()
    notes     = request.form.get("notes", "").strip()
    db        = get_db()
    ok, result = _apply_transaction(db, item_id, "checkin", qty, user_name, project, notes)
    if ok:
        flash(f'Checked in {qty} {result["unit"]} of "{result["name"]}".', "success")
    else:
        flash(result, "danger")
    db.close()
    return redirect(url_for("item_detail", item_id=item_id))


@app.route("/items/<int:item_id>/restock", methods=["POST"])
def restock(item_id):
    qty       = float(request.form.get("quantity", 1))
    user_name = request.form.get("user_name", "Unknown").strip() or "Unknown"
    notes     = request.form.get("notes", "").strip()
    db        = get_db()
    ok, result = _apply_transaction(db, item_id, "restock", qty, user_name, "", notes)
    if ok:
        flash(f'Restocked {qty} {result["unit"]} of "{result["name"]}".', "success")
    else:
        flash(result, "danger")
    db.close()
    return redirect(url_for("item_detail", item_id=item_id))


# ---------------------------------------------------------------------------
# QR code
# ---------------------------------------------------------------------------

@app.route("/items/<int:item_id>/qr")
def item_qr(item_id):
    url = request.host_url.rstrip("/") + url_for("item_detail", item_id=item_id)
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ---------------------------------------------------------------------------
# Transactions log
# ---------------------------------------------------------------------------

@app.route("/transactions")
def transactions():
    db     = get_db()
    action = request.args.get("action", "")
    sql    = """
        SELECT t.*, i.name AS item_name
        FROM transactions t
        JOIN items i ON t.item_id = i.id
        WHERE 1=1
    """
    params = []
    if action:
        sql += " AND t.action = ?"
        params.append(action)
    sql += " ORDER BY t.timestamp DESC LIMIT 200"
    txns = db.execute(sql, params).fetchall()
    db.close()
    return render_template("transactions.html", transactions=txns, selected_action=action)


# ---------------------------------------------------------------------------
# Categories & Locations management
# ---------------------------------------------------------------------------

@app.route("/categories", methods=["GET", "POST"])
def categories():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            try:
                db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
                db.commit()
                flash(f'Category "{name}" added.', "success")
            except Exception:
                flash(f'Category "{name}" already exists.', "danger")
    cats = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    db.close()
    return render_template("categories.html", categories=cats)


@app.route("/categories/<int:cat_id>/delete", methods=["POST"])
def delete_category(cat_id):
    db = get_db()
    cat = db.execute("SELECT name FROM categories WHERE id = ?", (cat_id,)).fetchone()
    if cat:
        db.execute("UPDATE items SET category_id=NULL WHERE category_id=?", (cat_id,))
        db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
        db.commit()
        flash(f'Category "{cat["name"]}" deleted.', "success")
    db.close()
    return redirect(url_for("categories"))


@app.route("/locations", methods=["GET", "POST"])
def locations():
    db = get_db()
    if request.method == "POST":
        name      = request.form.get("name", "").strip()
        parent_id = request.form.get("parent_id") or None
        if name:
            try:
                db.execute("INSERT INTO locations (name, parent_id) VALUES (?, ?)", (name, parent_id))
                db.commit()
                flash(f'Location "{name}" added.', "success")
            except Exception:
                flash(f'Location "{name}" already exists.', "danger")
    locs = db.execute("""
        SELECT l.*, p.name AS parent_name
        FROM locations l
        LEFT JOIN locations p ON l.parent_id = p.id
        ORDER BY COALESCE(p.name, l.name), l.parent_id NULLS FIRST, l.name
    """).fetchall()
    db.close()
    return render_template("locations.html", locations=locs)


@app.route("/locations/<int:loc_id>/delete", methods=["POST"])
def delete_location(loc_id):
    db = get_db()
    loc = db.execute("SELECT name FROM locations WHERE id = ?", (loc_id,)).fetchone()
    if loc:
        db.execute("UPDATE items SET location_id=NULL WHERE location_id=?", (loc_id,))
        db.execute("UPDATE kits SET location_id=NULL WHERE location_id=?", (loc_id,))
        db.execute("UPDATE locations SET parent_id=NULL WHERE parent_id=?", (loc_id,))
        db.execute("DELETE FROM locations WHERE id=?", (loc_id,))
        db.commit()
        flash(f'Location "{loc["name"]}" deleted.', "success")
    db.close()
    return redirect(url_for("locations"))


# ---------------------------------------------------------------------------
# Kits
# ---------------------------------------------------------------------------

@app.route("/kits")
def kits():
    db = get_db()
    kit_list = db.execute("""
        SELECT k.*, l.name AS location_name,
               COUNT(kc.id) AS component_count
        FROM kits k
        LEFT JOIN locations l ON k.location_id = l.id
        LEFT JOIN kit_components kc ON k.id = kc.kit_id
        GROUP BY k.id
        ORDER BY k.name
    """).fetchall()
    db.close()
    return render_template("kits.html", kits=kit_list)


@app.route("/kits/new", methods=["GET", "POST"])
def new_kit():
    db = get_db()
    if request.method == "POST":
        name      = request.form["name"].strip()
        kit_type  = request.form.get("kit_type", "custom")
        supplier  = request.form.get("supplier", "").strip()
        sku       = request.form.get("sku", "").strip()
        loc_id    = request.form.get("location_id") or None
        notes     = request.form.get("notes", "").strip()

        cur = db.execute("""
            INSERT INTO kits (name, kit_type, supplier, sku, location_id, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, kit_type, supplier, sku, loc_id, notes))
        kit_id = cur.lastrowid

        item_ids   = request.form.getlist("component_item_id")
        quantities = request.form.getlist("component_qty")
        for iid, qty in zip(item_ids, quantities):
            if iid and qty:
                db.execute("""
                    INSERT INTO kit_components (kit_id, item_id, quantity_per_kit)
                    VALUES (?, ?, ?)
                """, (kit_id, int(iid), float(qty)))

        db.commit()
        db.close()
        flash(f'Kit "{name}" created.', "success")
        return redirect(url_for("kit_detail", kit_id=kit_id))

    items     = db.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    locations = db.execute("SELECT * FROM locations ORDER BY name").fetchall()
    db.close()
    return render_template("kit_form.html", kit=None, items=items,
                           locations=locations, components=[])


@app.route("/kits/<int:kit_id>")
def kit_detail(kit_id):
    db = get_db()
    kit = db.execute("""
        SELECT k.*, l.name AS location_name
        FROM kits k
        LEFT JOIN locations l ON k.location_id = l.id
        WHERE k.id = ?
    """, (kit_id,)).fetchone()
    if not kit:
        flash("Kit not found.", "danger")
        return redirect(url_for("kits"))

    components = db.execute("""
        SELECT kc.*, i.name AS item_name, i.quantity AS item_quantity,
               i.unit AS item_unit, i.low_stock_threshold
        FROM kit_components kc
        JOIN items i ON kc.item_id = i.id
        WHERE kc.kit_id = ?
        ORDER BY i.name
    """, (kit_id,)).fetchall()

    history = db.execute("""
        SELECT * FROM kit_transactions WHERE kit_id = ?
        ORDER BY timestamp DESC LIMIT 30
    """, (kit_id,)).fetchall()

    max_buildable = None
    for comp in components:
        if comp["quantity_per_kit"] > 0:
            buildable = int(comp["item_quantity"] / comp["quantity_per_kit"])
            if max_buildable is None or buildable < max_buildable:
                max_buildable = buildable

    db.close()
    return render_template("kit_detail.html", kit=kit, components=components,
                           history=history, max_buildable=max_buildable if max_buildable is not None else 0)


@app.route("/kits/<int:kit_id>/edit", methods=["GET", "POST"])
def edit_kit(kit_id):
    db = get_db()
    kit = db.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if not kit:
        flash("Kit not found.", "danger")
        return redirect(url_for("kits"))

    if request.method == "POST":
        name     = request.form["name"].strip()
        kit_type = request.form.get("kit_type", "custom")
        supplier = request.form.get("supplier", "").strip()
        sku      = request.form.get("sku", "").strip()
        loc_id   = request.form.get("location_id") or None
        notes    = request.form.get("notes", "").strip()

        db.execute("""
            UPDATE kits SET name=?, kit_type=?, supplier=?, sku=?,
                            location_id=?, notes=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (name, kit_type, supplier, sku, loc_id, notes, kit_id))

        db.execute("DELETE FROM kit_components WHERE kit_id = ?", (kit_id,))
        item_ids   = request.form.getlist("component_item_id")
        quantities = request.form.getlist("component_qty")
        for iid, qty in zip(item_ids, quantities):
            if iid and qty:
                db.execute("""
                    INSERT INTO kit_components (kit_id, item_id, quantity_per_kit)
                    VALUES (?, ?, ?)
                """, (kit_id, int(iid), float(qty)))

        db.commit()
        db.close()
        flash(f'Kit "{name}" updated.', "success")
        return redirect(url_for("kit_detail", kit_id=kit_id))

    components = db.execute("""
        SELECT kc.*, i.name AS item_name
        FROM kit_components kc
        JOIN items i ON kc.item_id = i.id
        WHERE kc.kit_id = ?
    """, (kit_id,)).fetchall()
    items     = db.execute("SELECT id, name, unit FROM items ORDER BY name").fetchall()
    locations = db.execute("SELECT * FROM locations ORDER BY name").fetchall()
    db.close()
    return render_template("kit_form.html", kit=kit, items=items,
                           locations=locations, components=components)


@app.route("/kits/<int:kit_id>/delete", methods=["POST"])
def delete_kit(kit_id):
    db = get_db()
    kit = db.execute("SELECT name FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if kit:
        db.execute("DELETE FROM kit_transactions WHERE kit_id = ?", (kit_id,))
        db.execute("DELETE FROM kit_components WHERE kit_id = ?", (kit_id,))
        db.execute("DELETE FROM kits WHERE id = ?", (kit_id,))
        db.commit()
        flash(f'Kit "{kit["name"]}" deleted.', "success")
    db.close()
    return redirect(url_for("kits"))


@app.route("/kits/<int:kit_id>/build", methods=["POST"])
def build_kit(kit_id):
    """Assemble custom kits: deduct components, increment kit count."""
    db = get_db()
    kit = db.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if not kit:
        flash("Kit not found.", "danger")
        db.close()
        return redirect(url_for("kits"))

    qty       = max(1, int(request.form.get("quantity", 1)))
    user_name = request.form.get("user_name", "Unknown").strip() or "Unknown"
    notes     = request.form.get("notes", "").strip()

    components = db.execute("""
        SELECT kc.*, i.name AS item_name, i.quantity AS item_quantity, i.unit AS item_unit
        FROM kit_components kc
        JOIN items i ON kc.item_id = i.id
        WHERE kc.kit_id = ?
    """, (kit_id,)).fetchall()

    if not components:
        flash("Cannot build kit: no components defined.", "danger")
        db.close()
        return redirect(url_for("kit_detail", kit_id=kit_id))

    # Validate all components have sufficient stock
    for comp in components:
        needed = comp["quantity_per_kit"] * qty
        if comp["item_quantity"] < needed:
            flash(
                f'Not enough "{comp["item_name"]}" '
                f'(need {needed} {comp["item_unit"]}, have {comp["item_quantity"]}).',
                "danger"
            )
            db.close()
            return redirect(url_for("kit_detail", kit_id=kit_id))

    # Deduct each component
    for comp in components:
        needed = comp["quantity_per_kit"] * qty
        ok, result = _apply_transaction(
            db, comp["item_id"], "kit_build", -needed, user_name, "",
            f'Built {qty}x kit "{kit["name"]}"'
        )
        if not ok:
            flash(result, "danger")
            db.close()
            return redirect(url_for("kit_detail", kit_id=kit_id))

    # Increment kit stock
    qty_before = kit["quantity"]
    qty_after  = qty_before + qty
    db.execute("UPDATE kits SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
               (qty_after, kit_id))
    db.execute("""
        INSERT INTO kit_transactions
            (kit_id, action, quantity_change, quantity_before, quantity_after, user_name, notes)
        VALUES (?, 'build', ?, ?, ?, ?, ?)
    """, (kit_id, qty, qty_before, qty_after, user_name, notes))

    db.commit()
    db.close()
    flash(f'Assembled {qty} kit(s) of "{kit["name"]}". Components deducted from inventory.', "success")
    return redirect(url_for("kit_detail", kit_id=kit_id))


@app.route("/kits/<int:kit_id>/receive", methods=["POST"])
def receive_kit(kit_id):
    """Receive vendor-supplied kits (no component deduction)."""
    db = get_db()
    kit = db.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if not kit:
        flash("Kit not found.", "danger")
        db.close()
        return redirect(url_for("kits"))

    qty       = max(1, int(request.form.get("quantity", 1)))
    user_name = request.form.get("user_name", "Unknown").strip() or "Unknown"
    notes     = request.form.get("notes", "").strip()

    qty_before = kit["quantity"]
    qty_after  = qty_before + qty
    db.execute("UPDATE kits SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
               (qty_after, kit_id))
    db.execute("""
        INSERT INTO kit_transactions
            (kit_id, action, quantity_change, quantity_before, quantity_after, user_name, notes)
        VALUES (?, 'receive_vendor', ?, ?, ?, ?, ?)
    """, (kit_id, qty, qty_before, qty_after, user_name, notes))

    db.commit()
    db.close()
    flash(f'Received {qty} vendor kit(s) of "{kit["name"]}".', "success")
    return redirect(url_for("kit_detail", kit_id=kit_id))


@app.route("/kits/<int:kit_id>/checkout", methods=["POST"])
def checkout_kit(kit_id):
    db = get_db()
    kit = db.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if not kit:
        flash("Kit not found.", "danger")
        db.close()
        return redirect(url_for("kits"))

    qty       = max(1, int(request.form.get("quantity", 1)))
    user_name = request.form.get("user_name", "Unknown").strip() or "Unknown"
    project   = request.form.get("project", "").strip()
    notes     = request.form.get("notes", "").strip()

    qty_before = kit["quantity"]
    if qty_before < qty:
        flash(f'Not enough kits in stock (available: {qty_before}).', "danger")
        db.close()
        return redirect(url_for("kit_detail", kit_id=kit_id))

    qty_after = qty_before - qty
    db.execute("UPDATE kits SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
               (qty_after, kit_id))
    db.execute("""
        INSERT INTO kit_transactions
            (kit_id, action, quantity_change, quantity_before, quantity_after, user_name, project, notes)
        VALUES (?, 'checkout', ?, ?, ?, ?, ?, ?)
    """, (kit_id, -qty, qty_before, qty_after, user_name, project, notes))

    db.commit()
    db.close()
    flash(f'Checked out {qty} kit(s) of "{kit["name"]}".', "success")
    return redirect(url_for("kit_detail", kit_id=kit_id))


@app.route("/kits/<int:kit_id>/checkin", methods=["POST"])
def checkin_kit(kit_id):
    db = get_db()
    kit = db.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if not kit:
        flash("Kit not found.", "danger")
        db.close()
        return redirect(url_for("kits"))

    qty       = max(1, int(request.form.get("quantity", 1)))
    user_name = request.form.get("user_name", "Unknown").strip() or "Unknown"
    project   = request.form.get("project", "").strip()
    notes     = request.form.get("notes", "").strip()

    qty_before = kit["quantity"]
    qty_after  = qty_before + qty
    db.execute("UPDATE kits SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
               (qty_after, kit_id))
    db.execute("""
        INSERT INTO kit_transactions
            (kit_id, action, quantity_change, quantity_before, quantity_after, user_name, project, notes)
        VALUES (?, 'checkin', ?, ?, ?, ?, ?, ?)
    """, (kit_id, qty, qty_before, qty_after, user_name, project, notes))

    db.commit()
    db.close()
    flash(f'Returned {qty} kit(s) of "{kit["name"]}".', "success")
    return redirect(url_for("kit_detail", kit_id=kit_id))


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5001)
