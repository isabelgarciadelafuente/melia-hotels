"""
MELIA HOTELS — Data Loader (v5)
================================
Run AFTER schema.py.

Architecture principle (paired with schema.py):
  This file performs INSERTs only. No business math is computed in Python.
  The triggers in schema.py compute listed_price, total_paid, points_earned,
  populate BookingModifier, create the Invoice, mark RoomDay 'occupied',
  and credit MeliaRewards.

What changed in v5 vs v4:
  - Calendar window shifted from 2023–2025 to 2025–2027, so "today"
    (the real current date) sits inside the data range.
  - The 18 hardcoded manual bookings are gone. So are the 15 hardcoded
    customers and 8 hardcoded members that backed them. Customers and
    members are now generated only by the synthetic block.
  - Synthetic booking status is now determined by check_out vs today,
    not by a random roll:
        check_out < today  →  'completed'   (the stay has already happened)
        check_out >= today →  'confirmed'   (still in the future, cancellable)
        7 % chance of 'cancelled' regardless
    This produces a coherent dataset where no 'confirmed' booking has a
    check_out in the past.
  - PriceModifiers regenerated for 2025/26/27.
  - Year weights tilted toward the past so most bookings are 'completed'
    history rather than 'confirmed' future:
        YEAR_W = [0.45, 0.35, 0.20]  for [2025, 2026, 2027]

Insertion order:
  Block 1   Brands
  Block 2   Hotel
  Block 3   Room
  Block 4   RoomDay  (one INSERT-SELECT against v_calendar_days; no triggers)
  Block 5   PriceModifier  (drop trg_pm_* → bulk insert → bulk recompute
                            RoomDay → restore trg_pm_*)
  Block 6   Customer       (~19 015 customers: 19 000 direct + 15 agencies, generated)
  Block 7   MeliaRewards   (~3 420 members, ~18% of direct customers)
  Block 8   Booking        (~52 000 synthetic bookings, status by date)
"""

import sqlite3
import random
from pathlib import Path
from datetime import date, timedelta

# ── Config ─────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "melia.db"
TARGET  = 20_000               # synthetic bookings to generate
YEARS   = [2025, 2026, 2027]
YEAR_W  = [0.45, 0.35, 0.20]   # most weight on the past for realistic history
BATCH   = 2_000
TODAY   = date.today()         # the real current date — drives status logic
random.seed(42)
# ───────────────────────────────────────────────────────────────────────────

conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA foreign_keys     = ON")
conn.execute("PRAGMA journal_mode     = WAL")
conn.execute("PRAGMA recursive_triggers = OFF")
cur = conn.cursor()


# ============================================================
# BLOCK 1: Brands
# ============================================================
print("Block 1: Brands...")
cur.executemany(
    "INSERT INTO Brands (brand_id,name,segment,description,target_audience) VALUES (?,?,?,?,?)",
    [
        (1,'Gran Meliá',       'Luxury',   'Classic Spanish elegance and Mediterranean culture; high-end haute cuisine.','Affluent travelers & luxury seekers'),
        (2,'Meliá Hotels',     'Premium',  'Flagship mainstream brand; full-service premium hotels in top destinations.','Business & leisure travelers'),
        (3,'ME by Meliá',      'Luxury',   'Minimalist design with artistic touches inspired by contemporary culture.',  'Trendy urban travelers & creatives'),
        (4,'Innside by Meliá', 'Premium',  'Urban & beach hotels with a contemporary lifestyle feel and local culture.', 'Modern urban travelers'),
        (5,'Sol by Meliá',     'Essential','Fun, family-friendly resorts focused on holidays in coastal destinations.',  'Families & sun-and-sea vacationers'),
    ]
)
conn.commit()
print(f"  ✓ {cur.execute('SELECT COUNT(*) FROM Brands').fetchone()[0]} brands.")


# ============================================================
# BLOCK 2: Hotel  (20 hotels across 5 brands)
# ============================================================
print("Block 2: Hotels...")
cur.executemany(
    "INSERT INTO Hotel (hotel_id,name,country,city,brand_id) VALUES (?,?,?,?,?)",
    [
        (1, 'Gran Meliá Palacio de los Duques','Spain','Madrid',    1),
        (2, 'Meliá Barcelona Sarria',          'Spain','Barcelona', 2),
        (3, 'ME Madrid Reina Victoria',        'Spain','Madrid',    3),
        (4, 'Innside Madrid Luchana',          'Spain','Madrid',    4),
        (5, 'Sol Lanzarote',                   'Spain','Lanzarote', 5),
        (6, 'Gran Meliá Rome',     'Italy',         'Rome',         1),
        (7, 'Gran Meliá Dubai',    'UAE',           'Dubai',        1),
        (8, 'Gran Meliá Shanghai', 'China',         'Shanghai',     1),
        (9, 'Meliá Paris Opera',   'France',        'Paris',        2),
        (10,'Meliá Berlin',        'Germany',       'Berlin',       2),
        (11,'Meliá Amsterdam',     'Netherlands',   'Amsterdam',    2),
        (12,'ME London',           'UK',            'London',       3),
        (13,'ME Ibiza',            'Spain',         'Ibiza',        3),
        (14,'ME Dubai',            'UAE',           'Dubai',        3),
        (15,'Innside Prague',      'Czech Republic','Prague',       4),
        (16,'Innside Amsterdam',   'Netherlands',   'Amsterdam',    4),
        (17,'Innside Munich',      'Germany',       'Munich',       4),
        (18,'Sol Tenerife',        'Spain',         'Tenerife',     5),
        (19,'Sol Mallorca',        'Spain',         'Mallorca',     5),
        (20,'Sol Fuerteventura',   'Spain',         'Fuerteventura',5),
    ]
)
conn.commit()
print(f"  ✓ {cur.execute('SELECT COUNT(*) FROM Hotel').fetchone()[0]} hotels.")


# ============================================================
# BLOCK 3: Room  (50 per hotel = 1 000 total)
# ============================================================
print("Block 3: Rooms...")

TEMPLATES = {
    1: [('Deluxe',      350.0, 15), ('Suite',      650.0, 20),
        ('Junior Suite',900.0, 10), ('Penthouse', 1500.0,  5)],
    2: [('Standard',    150.0, 20), ('Deluxe',     220.0, 18),
        ('Suite',       380.0, 10), ('Junior Suite',550.0,  2)],
    3: [('Deluxe',      280.0, 15), ('Suite',      520.0, 20),
        ('Junior Suite',750.0, 10), ('Penthouse', 1200.0,  5)],
    4: [('Standard',     90.0, 22), ('Deluxe',     140.0, 18),
        ('Suite',       250.0, 10)],
    5: [('Standard',     80.0, 25), ('Deluxe',     130.0, 18),
        ('Suite',       210.0,  7)],
}
GUESTS_BY_TYPE = {'Standard':2,'Deluxe':2,'Suite':3,'Junior Suite':3,'Penthouse':4}

cur.execute("SELECT hotel_id, brand_id FROM Hotel")
all_hotels = cur.fetchall()

rid = 1
all_rooms = []
for hotel_id, brand_id in all_hotels:
    floor = 1
    full_list = []
    for rtype, base, count in TEMPLATES[brand_id]:
        for i in range(count):
            full_list.append((rtype, base, f"{floor}{i+1:02d}"))
        floor += 1
    for rtype, base, rnum in full_list[:50]:
        varied = round(base * random.uniform(0.90, 1.10), 2)
        guests = GUESTS_BY_TYPE.get(rtype, 2)
        all_rooms.append((rid, rnum, rtype, varied, guests, hotel_id))
        rid += 1

for i in range(0, len(all_rooms), BATCH):
    cur.executemany(
        "INSERT INTO Room(room_id,room_number,room_type,base_rate,max_guests,hotel_id) VALUES(?,?,?,?,?,?)",
        all_rooms[i:i+BATCH]
    )
conn.commit()
total_rooms = cur.execute('SELECT COUNT(*) FROM Room').fetchone()[0]
print(f"  ✓ {total_rooms:,} rooms.")


# ============================================================
# BLOCK 4: RoomDay  (calendar via INSERT-SELECT — no Python math)
# ============================================================
print("Block 4: RoomDay (calendar)...")
cur.execute("""
INSERT INTO RoomDay (room_id, day, price_per_day, status)
SELECT r.room_id, d.day, r.base_rate, 'available'
FROM   Room            r
CROSS  JOIN v_calendar_days d
""")
conn.commit()
total_rd = cur.execute('SELECT COUNT(*) FROM RoomDay').fetchone()[0]
print(f"  ✓ {total_rd:,} RoomDay rows (price_per_day = base_rate; modifiers next).")


# ============================================================
# BLOCK 5: PriceModifier  (44 rows) — bulk-load optimisation
# ============================================================
print("Block 5: PriceModifier (with bulk-recompute optimization)...")

print("  Temporarily dropping trg_pm_* triggers...")
saved_pm_triggers = {}
for tname in ('trg_pm_after_insert', 'trg_pm_after_update', 'trg_pm_after_delete'):
    row = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
        (tname,)
    ).fetchone()
    if row is not None:
        saved_pm_triggers[tname] = row[0]
        cur.execute(f"DROP TRIGGER {tname}")
print(f"  ✓ {len(saved_pm_triggers)} triggers dropped.")

cur.executemany(
    """INSERT INTO PriceModifier
       (modifier_id,name,modifier_type,value,start_date,end_date,
        hotel_id,min_points_required,weekends_only)
       VALUES (?,?,?,?,?,?,?,?,?)""",
    [
        # ──────────── 2025 PUBLIC MODIFIERS ────────────
        (1, 'Temporada Alta Verano 2025',              'percentage',  0.25, '2025-06-01','2025-08-31', None, None, None),
        (2, 'Temporada Baja Invierno 2025-26',         'percentage', -0.15, '2025-11-01','2026-02-28', None, None, None),
        (3, 'Tarifa Fin de Semana ME — Madrid 2025',   'percentage',  0.10, '2025-01-01','2025-12-31', 3,    None, 1),
        (4, 'Oferta Early Bird 2025',                  'percentage', -0.12, '2025-01-01','2025-05-31', None, None, None),
        (5, 'Descuento Innside Directo 2025',          'percentage', -0.08, '2025-01-01','2025-12-31', 4,    None, None),
        (10,'Cargo Limpieza Sol — Lanzarote 2025',     'fixed',       5.00, '2025-01-01','2025-12-31', 5,    None, None),
        # 2025 brand-wide duplicates — ME weekend rate
        (32,'Tarifa Fin de Semana ME — London 2025',   'percentage',  0.10, '2025-01-01','2025-12-31', 12,   None, 1),
        (33,'Tarifa Fin de Semana ME — Ibiza 2025',    'percentage',  0.10, '2025-01-01','2025-12-31', 13,   None, 1),
        (34,'Tarifa Fin de Semana ME — Dubai 2025',    'percentage',  0.10, '2025-01-01','2025-12-31', 14,   None, 1),
        # 2025 brand-wide duplicates — Sol cleaning fee
        (35,'Cargo Limpieza Sol — Tenerife 2025',      'fixed',       5.00, '2025-01-01','2025-12-31', 18,   None, None),
        (36,'Cargo Limpieza Sol — Mallorca 2025',      'fixed',       5.00, '2025-01-01','2025-12-31', 19,   None, None),
        (37,'Cargo Limpieza Sol — Fuerteventura 2025', 'fixed',       5.00, '2025-01-01','2025-12-31', 20,   None, None),

        # ──────────── MEMBER-ONLY MODIFIERS (cover full 2025-2027) ────────────
        (6, 'Descuento Miembro Básico',                'percentage', -0.05, '2025-01-01','2027-12-31', None, 0,     None),
        (7, 'Descuento Miembro Silver',                'percentage', -0.10, '2025-01-01','2027-12-31', None, 5000,  None),
        (8, 'Descuento Miembro Gold',                  'percentage', -0.15, '2025-01-01','2027-12-31', None, 15000, None),
        (9, 'Descuento Miembro Plat.',                 'percentage', -0.20, '2025-01-01','2027-12-31', None, 30000, None),
        (11,'Bono Bienvenida Sol — Lanzarote',         'fixed',     -10.00, '2025-01-01','2027-12-31', 5,    0,     None),
        (38,'Bono Bienvenida Sol — Tenerife',          'fixed',     -10.00, '2025-01-01','2027-12-31', 18,   0,     None),
        (39,'Bono Bienvenida Sol — Mallorca',          'fixed',     -10.00, '2025-01-01','2027-12-31', 19,   0,     None),
        (40,'Bono Bienvenida Sol — Fuerteventura',     'fixed',     -10.00, '2025-01-01','2027-12-31', 20,   0,     None),

        # ──────────── 2026 PUBLIC MODIFIERS ────────────
        (20,'Temporada Alta Verano 2026',              'percentage',  0.25, '2026-06-01','2026-08-31', None, None, None),
        (21,'Temporada Baja Invierno 2026-27',         'percentage', -0.15, '2026-11-01','2027-02-28', None, None, None),
        (22,'Oferta Early Bird 2026',                  'percentage', -0.12, '2026-01-01','2026-05-31', None, None, None),
        (23,'Tarifa Fin de Semana ME — Madrid 2026',   'percentage',  0.10, '2026-01-01','2026-12-31', 3,    None, 1),
        (24,'Descuento Innside Directo 2026',          'percentage', -0.08, '2026-01-01','2026-12-31', 4,    None, None),
        (25,'Cargo Limpieza Sol — Lanzarote 2026',     'fixed',       5.00, '2026-01-01','2026-12-31', 5,    None, None),
        # 2026 brand-wide duplicates — ME weekend rate
        (41,'Tarifa Fin de Semana ME — London 2026',   'percentage',  0.10, '2026-01-01','2026-12-31', 12,   None, 1),
        (42,'Tarifa Fin de Semana ME — Ibiza 2026',    'percentage',  0.10, '2026-01-01','2026-12-31', 13,   None, 1),
        (43,'Tarifa Fin de Semana ME — Dubai 2026',    'percentage',  0.10, '2026-01-01','2026-12-31', 14,   None, 1),
        # 2026 brand-wide duplicates — Sol cleaning fee
        (44,'Cargo Limpieza Sol — Tenerife 2026',      'fixed',       5.00, '2026-01-01','2026-12-31', 18,   None, None),
        (45,'Cargo Limpieza Sol — Mallorca 2026',      'fixed',       5.00, '2026-01-01','2026-12-31', 19,   None, None),
        (46,'Cargo Limpieza Sol — Fuerteventura 2026', 'fixed',       5.00, '2026-01-01','2026-12-31', 20,   None, None),

        # ──────────── 2027 PUBLIC MODIFIERS ────────────
        (26,'Temporada Alta Verano 2027',              'percentage',  0.25, '2027-06-01','2027-08-31', None, None, None),
        (27,'Temporada Baja Invierno 2027',            'percentage', -0.15, '2027-11-01','2027-12-31', None, None, None),
        (28,'Oferta Early Bird 2027',                  'percentage', -0.12, '2027-01-01','2027-05-31', None, None, None),
        (29,'Tarifa Fin de Semana ME — Madrid 2027',   'percentage',  0.10, '2027-01-01','2027-12-31', 3,    None, 1),
        (30,'Descuento Innside Directo 2027',          'percentage', -0.08, '2027-01-01','2027-12-31', 4,    None, None),
        (31,'Cargo Limpieza Sol — Lanzarote 2027',     'fixed',       5.00, '2027-01-01','2027-12-31', 5,    None, None),
        # 2027 brand-wide duplicates — ME weekend rate
        (47,'Tarifa Fin de Semana ME — London 2027',   'percentage',  0.10, '2027-01-01','2027-12-31', 12,   None, 1),
        (48,'Tarifa Fin de Semana ME — Ibiza 2027',    'percentage',  0.10, '2027-01-01','2027-12-31', 13,   None, 1),
        (49,'Tarifa Fin de Semana ME — Dubai 2027',    'percentage',  0.10, '2027-01-01','2027-12-31', 14,   None, 1),
        # 2027 brand-wide duplicates — Sol cleaning fee
        (50,'Cargo Limpieza Sol — Tenerife 2027',      'fixed',       5.00, '2027-01-01','2027-12-31', 18,   None, None),
        (51,'Cargo Limpieza Sol — Mallorca 2027',      'fixed',       5.00, '2027-01-01','2027-12-31', 19,   None, None),
        (52,'Cargo Limpieza Sol — Fuerteventura 2027', 'fixed',       5.00, '2027-01-01','2027-12-31', 20,   None, None),
    ]
)
conn.commit()
total_pm = cur.execute('SELECT COUNT(*) FROM PriceModifier').fetchone()[0]
print(f"  ✓ {total_pm} PriceModifiers inserted (RoomDay still at base_rate).")

print("  Bulk-recomputing RoomDay prices via v_room_day_price...")
cur.execute("""
    UPDATE RoomDay
    SET    price_per_day = (
        SELECT computed_price
        FROM   v_room_day_price
        WHERE  room_id = RoomDay.room_id
          AND  day     = RoomDay.day
    )
""")
conn.commit()
print("  ✓ RoomDay fully recomputed.")

print("  Restoring trg_pm_* triggers...")
for sql in saved_pm_triggers.values():
    cur.execute(sql)
conn.commit()
print(f"  ✓ {len(saved_pm_triggers)} triggers restored.")


# ============================================================
# BLOCK 6: Customer  (~1 500 direct individuals + 15 agencies)
# ============================================================
# Names are generated from per-nationality pools so the dataset looks
# realistic without requiring 1 500 hardcoded rows.
# ============================================================
print("Block 6: Customers...")

# ── Name pools by nationality ──────────────────────────────────────────────
NAME_POOLS = {
    'Spanish':    (['Carlos','Elena','Miguel','Laura','Javier','Sofia','Pablo','Carmen',
                    'David','Isabel','Alejandro','Lucia','Fernando','Ana','Diego'],
                   ['García','López','Martínez','Sánchez','Romero','Torres','Díaz',
                    'Ruiz','Pérez','Moreno','Jiménez','Álvarez','Muñoz','Herrera']),
    'British':    (['Oliver','Amelia','Jack','Emily','Harry','Isla','George','Ava',
                    'Charlie','Lily','James','Grace','William','Sophie','Thomas'],
                   ['Smith','Jones','Taylor','Brown','Wilson','Evans','Davies','White',
                    'Thomas','Johnson','Roberts','Walker','Robinson','Clarke','Hall']),
    'German':     (['Lukas','Hannah','Max','Emma','Paul','Mia','Felix','Laura','Jonas',
                    'Lena','Leon','Anna','Moritz','Sophie','Tim'],
                   ['Müller','Schmidt','Schneider','Fischer','Weber','Meyer','Wagner',
                    'Becker','Schulz','Hoffmann','Schäfer','Koch','Bauer','Richter']),
    'French':     (['Léo','Emma','Louis','Chloé','Lucas','Camille','Hugo','Manon',
                    'Théo','Inès','Nathan','Jade','Tom','Léa','Antoine'],
                   ['Martin','Bernard','Dubois','Thomas','Robert','Richard','Petit',
                    'Durand','Leroy','Moreau','Simon','Laurent','Lefebvre','Michel']),
    'Italian':    (['Marco','Giulia','Luca','Sofia','Matteo','Aurora','Lorenzo','Chiara',
                    'Alessandro','Martina','Francesco','Giorgia','Andrea','Valentina','Davide'],
                   ['Rossi','Russo','Ferrari','Esposito','Bianchi','Romano','Colombo',
                    'Ricci','Marino','Greco','Bruno','Gallo','Conti','De Luca','Mancini']),
    'American':   (['James','Emma','Liam','Olivia','Noah','Ava','William','Sophia',
                    'Mason','Isabella','Ethan','Mia','Alexander','Charlotte','Michael'],
                   ['Johnson','Williams','Brown','Jones','Garcia','Miller','Davis',
                    'Wilson','Anderson','Taylor','Thomas','Jackson','White','Harris']),
    'Swedish':    (['Erik','Anna','Karl','Emma','Johan','Maria','Lars','Sara','Anders',
                    'Lena','Mikael','Karin','Peter','Ingrid','Magnus'],
                   ['Johansson','Andersson','Karlsson','Nilsson','Eriksson','Larsson',
                    'Olsson','Persson','Svensson','Gustafsson','Pettersson','Lindqvist']),
    'Brazilian':  (['Lucas','Julia','Pedro','Ana','Gabriel','Beatriz','Matheus','Larissa',
                    'Rafael','Fernanda','Felipe','Camila','Bruno','Mariana','Gustavo'],
                   ['Silva','Santos','Oliveira','Souza','Rodrigues','Ferreira','Alves',
                    'Pereira','Lima','Carvalho','Gomes','Costa','Ribeiro','Martins']),
    'Chinese':    (['Wei','Fang','Lei','Jing','Tao','Xue','Ming','Yan','Hao','Lin',
                    'Rui','Ting','Chao','Mei','Jun'],
                   ['Wang','Li','Zhang','Liu','Chen','Yang','Huang','Zhao','Wu',
                    'Zhou','Sun','Ma','Zhu','He','Guo']),
    'Japanese':   (['Kenji','Yuki','Takashi','Aoi','Hiroshi','Sakura','Daisuke','Hana',
                    'Ryo','Nana','Shota','Rina','Kazuki','Yuna','Haruki'],
                   ['Sato','Suzuki','Tanaka','Watanabe','Ito','Yamamoto','Nakamura',
                    'Kobayashi','Kato','Yoshida','Yamada','Sasaki','Yamaguchi','Saito']),
    'Korean':     (['Minjun','Soyeon','Jihun','Jiyeon','Hyunwoo','Minji','Seungho',
                    'Eunji','Junho','Yuna','Donghyun','Seojun','Jiwon','Chaeyeon','Taehyung'],
                   ['Kim','Lee','Park','Choi','Jung','Kang','Cho','Yoon','Jang',
                    'Im','Han','Oh','Shin','Seo','Kwon']),
    'Dutch':      (['Daan','Emma','Sem','Sophie','Liam','Julia','Lucas','Lotte','Finn',
                    'Nora','Jesse','Olivia','Noah','Lisa','Bram'],
                   ['De Jong','Jansen','De Vries','Van den Berg','Van Dijk','Bakker',
                    'Janssen','Visser','Smit','Meijer','De Boer','Mulder','Bos','Peters']),
    'Polish':     (['Jakub','Zofia','Mateusz','Julia','Kamil','Anna','Piotr','Natalia',
                    'Michał','Karolina','Bartosz','Magdalena','Krzysztof','Aleksandra','Łukasz'],
                   ['Kowalski','Nowak','Wiśniewski','Wójcik','Kowalczyk','Kamiński',
                    'Lewandowski','Zielinski','Szymanski','Wozniak','Kozlowski','Jankowski']),
    'Russian':    (['Dmitri','Anna','Alexei','Maria','Sergei','Natasha','Ivan','Olga',
                    'Nikolai','Elena','Pavel','Irina','Andrei','Tatiana','Vladimir'],
                   ['Ivanov','Petrov','Sidorov','Smirnov','Kuznetsov','Popov','Sokolov',
                    'Volkov','Morozov','Novikov','Fedorov','Mikhailov','Lebedev','Semyonov']),
    'Australian': (['Liam','Olivia','Noah','Ava','Jack','Charlotte','Oliver','Isla',
                    'William','Mia','James','Amelia','Lucas','Grace','Henry'],
                   ['Smith','Jones','Williams','Brown','Wilson','Taylor','Johnson',
                    'White','Martin','Anderson','Thompson','Harris','Robinson','Walker']),
    'Argentine':  (['Santiago','Valentina','Matías','Sofía','Nicolás','Florencia','Lucas',
                    'Camila','Tomás','Agustina','Federico','Julieta','Gonzalo','Romina','Facundo'],
                   ['González','Fernández','López','Martínez','García','Rodríguez','Sánchez',
                    'Romero','Flores','Díaz','Torres','Morales','Ortiz','Álvarez','Rojas']),
    'Emirati':    (['Ahmed','Fatima','Mohammed','Aisha','Sultan','Mariam','Khalid','Sara',
                    'Hamdan','Hessa','Saif','Latifa','Rashid','Noura','Zayed'],
                   ['Al-Rashid','Al-Maktoum','Al-Nahyan','Al-Mansouri','Al-Shamsi',
                    'Al-Zaabi','Al-Kaabi','Al-Marzooqi','Al-Nuaimi','Al-Suwaidi']),
    'Mexican':    (['José','María','Juan','Ana','Carlos','Guadalupe','Luis','Rosa',
                    'Jorge','Patricia','Eduardo','Verónica','Ricardo','Claudia','Roberto'],
                   ['García','Martínez','López','González','Rodríguez','Hernández',
                    'Pérez','Sánchez','Ramírez','Cruz','Morales','Torres','Reyes','Flores']),
}

# Nationality distribution — weighted toward Meliá's core markets
NAT_POOL = (
    ['Spanish']*22 + ['British']*12 + ['German']*11 + ['French']*10 + ['Italian']*10 +
    ['American']*9  + ['Swedish']*5  + ['Dutch']*5   + ['Brazilian']*4 + ['Polish']*3 +
    ['Russian']*3   + ['Australian']*3 + ['Chinese']*4 + ['Japanese']*3 + ['Korean']*3 +
    ['Argentine']*2 + ['Emirati']*2  + ['Mexican']*2
)

EMAIL_DOMAINS = {
    'Spanish':'gmail.es',  'British':'gmail.co.uk', 'German':'gmail.de',
    'French':'gmail.fr',   'Italian':'gmail.it',    'American':'gmail.com',
    'Swedish':'gmail.se',  'Dutch':'gmail.nl',      'Brazilian':'gmail.com.br',
    'Polish':'gmail.pl',   'Russian':'mail.ru',     'Australian':'gmail.com.au',
    'Chinese':'163.com',   'Japanese':'gmail.jp',   'Korean':'naver.com',
    'Argentine':'gmail.com.ar','Emirati':'gmail.ae','Mexican':'gmail.com.mx',
}

N_DIRECT  = 19_000
AGENCIES  = [
    ('Global Travel Partners',     'American',    0.12, 'info@globaltravel.us'),
    ('Eurotrip Agency',            'German',      0.10, 'booking@eurotrip.de'),
    ('Asia Pacific Tours',         'Chinese',     0.15, 'res@asiapacific.sg'),
    ('Luxury Escapes Ltd',         'British',     0.08, 'res@luxuryescapes.co.uk'),
    ('Sun & Sea Holidays',         'Spanish',     0.12, 'info@sunsea.es'),
    ('Nordic Travel Group',        'Swedish',     0.10, 'book@nordictravel.se'),
    ('Premier Voyages',            'French',      0.15, 'contact@premiervoyages.fr'),
    ('Elite Destinations',         'Italian',     0.10, 'res@elitedest.it'),
    ('World Explorer Agency',      'Australian',  0.12, 'info@worldexplorer.au'),
    ('Corporate Travel Solutions', 'Swiss',       0.08, 'corp@ctstravel.ch'),
    ('Iberia Group Bookings',      'Spanish',     0.11, 'groups@iberiatravel.es'),
    ('Mediterranean Tours',        'Italian',     0.09, 'book@medtours.it'),
    ('Orient Express Travel',      'Japanese',    0.13, 'res@orientexpress.jp'),
    ('Atlantic Holidays',          'Brazilian',   0.10, 'info@atlanticholidays.br'),
    ('Dubai Luxury Travel',        'Emirati',     0.14, 'vip@dubailuxury.ae'),
]

# Each direct customer gets a segment affinity — stored in Python only,
# used during booking generation so luxury-preferring guests mostly book
# luxury hotels, etc.
# Distribution: 15% Luxury, 35% Premium, 50% Essential
SEGMENT_AFFINITY_POOL = ['Luxury']*15 + ['Premium']*35 + ['Essential']*50
cust_segment_pref = {}   # customer_id → preferred segment

customers_to_insert = []
cid = 1

# Direct customers — generated from name pools
for _ in range(N_DIRECT):
    nat   = random.choice(NAT_POOL)
    pool  = NAME_POOLS.get(nat, NAME_POOLS['British'])
    first = random.choice(pool[0])
    last  = random.choice(pool[1])
    name  = f"{first} {last}"
    base  = f"{first.lower().replace(' ','')}." \
            f"{last.lower().replace(' ','').replace('-','')}"
    email = f"{base}{cid}@{EMAIL_DOMAINS.get(nat,'gmail.com')}"
    customers_to_insert.append((cid, name, 'direct', email, nat, 0.00))
    cust_segment_pref[cid] = random.choice(SEGMENT_AFFINITY_POOL)
    cid += 1

# Agencies
for (aname, anat, comm, aemail) in AGENCIES:
    customers_to_insert.append((cid, aname, 'agency', aemail, anat, comm))
    cid += 1

for i in range(0, len(customers_to_insert), BATCH):
    cur.executemany(
        "INSERT INTO Customer (customer_id,name,customer_type,email,nationality,commission_rate) VALUES (?,?,?,?,?,?)",
        customers_to_insert[i:i+BATCH]
    )
conn.commit()
n_cust = cur.execute('SELECT COUNT(*) FROM Customer').fetchone()[0]
print(f"  ✓ {n_cust:,} customers ({N_DIRECT} direct individuals + {len(AGENCIES)} agencies).")


# ============================================================
# BLOCK 7: MeliaRewards  (~18% of direct customers enrolled)
# ============================================================
print("Block 7: MeliaRewards...")

direct_ids_for_enrol = [r[0] for r in cur.execute(
    "SELECT customer_id FROM Customer WHERE customer_type = 'direct' ORDER BY customer_id"
).fetchall()]
# ~18% enrolment rate — realistic for a loyalty programme
to_enroll = random.sample(direct_ids_for_enrol, int(len(direct_ids_for_enrol) * 0.18))


def rand_points():
    """Tier mix: 50 % Basic, 25 % Silver, 15 % Gold, 10 % Platinum."""
    r = random.random()
    if r < 0.10: return random.randint(30_000, 80_000)   # Platinum
    if r < 0.25: return random.randint(15_000, 29_999)   # Gold
    if r < 0.50: return random.randint(5_000,  14_999)   # Silver
    return random.randint(0, 4_999)                       # Basic


mid = 1
new_members = []
for mcid in to_enroll:
    yr = random.randint(2020, 2024)
    mo = random.randint(1, 12)
    dy = random.randint(1, 28)
    new_members.append((mid, mcid, rand_points(), f"{yr}-{mo:02d}-{dy:02d}"))
    mid += 1

for i in range(0, len(new_members), BATCH):
    cur.executemany(
        "INSERT INTO MeliaRewards (member_id,customer_id,points_balance,join_date) VALUES (?,?,?,?)",
        new_members[i:i+BATCH]
    )
conn.commit()
n_mem = cur.execute('SELECT COUNT(*) FROM MeliaRewards').fetchone()[0]
print(f"  ✓ {n_mem:,} members enrolled ({n_mem/N_DIRECT*100:.1f}% of direct customers).")


# ============================================================
# BLOCK 8: Booking  (~52 000 synthetic bookings, status by date)
# ============================================================
# Status assignment is no longer random. It's determined by check_out vs
# today:
#       check_out < today  →  'completed'
#       check_out >= today →  'confirmed'
#       7 % chance of 'cancelled' regardless
# The form-app and the cancel page rely on this invariant: a 'confirmed'
# booking is always still in the future (or in progress).
# ============================================================
print(f"Block 8: Generating {TARGET:,} synthetic bookings (today = {TODAY})...")

MONTH_W = [0.5, 0.6, 0.8, 1.0, 1.1, 1.3, 1.5, 1.5, 1.2, 1.0, 0.6, 0.8]
def rand_date(year):
    pool, weights = [], []
    for m in range(1, 13):
        ny, nm = (year, m + 1) if m < 12 else (year + 1, 1)
        days = (date(ny, nm, 1) - date(year, m, 1)).days
        for d in range(1, days + 1):
            pool.append(date(year, m, d))
            weights.append(MONTH_W[m - 1])
    return random.choices(pool, weights=weights)[0]

# Channel weights per segment — realistic 2025 distribution:
#   Luxury:    direct-heavy (relationship-driven), some agency, less web
#   Premium:   web + app dominant, some direct/agency
#   Essential: web + agency dominant (package tours), least direct
CHANNEL_SEG = {
    'Luxury':    ['direct']*4 + ['web']*2 + ['app']*2 + ['agency']*2,
    'Premium':   ['web']*4 + ['app']*3 + ['direct']*2 + ['agency']*1,
    'Essential': ['web']*4 + ['agency']*3 + ['app']*2 + ['direct']*1,
}
# (nights distribution moved to NIGHTS_OPTIONS below, with per-type weights)
CANCEL_REASONS = ['Change of travel plans','Medical emergency',
                  'Flight cancellation','Work conflict','Personal reasons']

# Lookup tables
cur.execute("SELECT b.brand_id, b.segment FROM Brands b")
b2s = dict(cur.fetchall())
cur.execute("SELECT h.hotel_id, h.brand_id FROM Hotel h")
h2b = dict(cur.fetchall())
cur.execute("SELECT room_id, room_type, hotel_id, max_guests FROM Room")
rooms_full = [(rid, rtype, hid, b2s[h2b[hid]], mg) for rid, rtype, hid, mg in cur.fetchall()]
cur.execute("SELECT customer_id, customer_type FROM Customer")
agency_ids = [cid for cid, ct in cur.fetchall() if ct == 'agency']
cur.execute("SELECT customer_id, customer_type FROM Customer")
direct_ids = [cid for cid, ct in cur.fetchall() if ct == 'direct']

# ── Customer booking-frequency weights ────────────────────────────────────
# With 19 000 customers and 20 000 bookings, most people book once.
# A small group of frequent travellers (~5%) books repeatedly.
# Weight 1 = occasional, 3 = moderate, 10 = frequent traveller.
def _customer_weights(ids, seed):
    rng = random.Random(seed)
    w = []
    for _ in ids:
        r = rng.random()
        if r < 0.05:   w.append(10)   # frequent traveller
        elif r < 0.20: w.append(3)    # books a few times a year
        else:          w.append(1)    # books once (or never gets picked)
    return w

direct_weights = _customer_weights(direct_ids, seed=7)
agency_weights = _customer_weights(agency_ids, seed=13)


# ── Rooms grouped by segment — for segment-affinity booking ───────────────
rooms_by_segment = {}
for row in rooms_full:
    rooms_by_segment.setdefault(row[3], []).append(row)   # row[3] = segment


# ── Lead time by channel ───────────────────────────────────────────────────
# App users are the most impulsive, agencies plan the furthest ahead.
def rand_lead(channel):
    r = random.random()
    if channel == 'app':
        # Very short — impulse / last-minute
        if r < 0.35: return random.randint(0, 3)
        elif r < 0.65: return random.randint(4, 14)
        elif r < 0.88: return random.randint(14, 45)
        else:          return random.randint(45, 90)
    elif channel == 'web':
        # Short to medium — browse and book
        if r < 0.15: return random.randint(0, 7)
        elif r < 0.50: return random.randint(7, 30)
        elif r < 0.82: return random.randint(30, 75)
        else:          return random.randint(75, 130)
    elif channel == 'direct':
        # Medium — people who call ahead tend to plan more
        if r < 0.08: return random.randint(0, 7)
        elif r < 0.30: return random.randint(7, 30)
        elif r < 0.70: return random.randint(30, 90)
        else:          return random.randint(90, 160)
    else:  # agency — package tours, planned well in advance
        if r < 0.05: return random.randint(0, 14)
        elif r < 0.20: return random.randint(14, 60)
        elif r < 0.65: return random.randint(60, 120)
        else:          return random.randint(120, 180)


# ── Nights by segment ─────────────────────────────────────────────────────
# Luxury: special occasion, longer stays. Premium: mix of business/leisure.
# Essential (Sol resorts): holiday mode, typically week-long.
NIGHTS_SEG = {
    'Luxury':    ([2, 3, 4, 5, 6, 7, 8, 9, 10], [1, 3, 4, 4, 3, 2, 1, 1, 1]),
    'Premium':   ([1, 2, 3, 4, 5, 6, 7],         [3, 5, 5, 4, 3, 1, 1]),
    'Essential': ([4, 5, 6, 7, 8, 9, 10, 14],    [2, 3, 4, 4, 3, 2, 1, 1]),
}


occupied = set()
synthetic_bookings = []
generated, attempts = 0, 0
while generated < TARGET and attempts < TARGET * 20:
    attempts += 1

    year     = random.choices(YEARS, weights=YEAR_W)[0]
    channel  = None   # determined after customer pick

    # ── Pick customer first, then derive segment preference ───────────────
    is_agency = random.random() < 0.18   # ~18% of bookings come via agencies
    if is_agency:
        customer_id = random.choices(agency_ids, weights=agency_weights)[0]
        segment     = random.choices(['Luxury','Premium','Essential'],
                                     weights=[15, 35, 50])[0]
        channel     = 'agency'
    else:
        customer_id = random.choices(direct_ids, weights=direct_weights)[0]
        # 80% of the time book in their preferred segment, 20% go elsewhere
        pref = cust_segment_pref[customer_id]
        if random.random() < 0.80:
            segment = pref
        else:
            segment = random.choice(['Luxury','Premium','Essential'])
        # Channel depends on segment (and reflects booking habits)
        channel = random.choice(CHANNEL_SEG[segment])

    # Pick a room from that segment
    room_id, rtype, hotel_id, _, max_guests = random.choice(rooms_by_segment[segment])

    check_in  = rand_date(year)
    opts, wts = NIGHTS_SEG[segment]
    nights    = random.choices(opts, weights=wts)[0]
    check_out = check_in + timedelta(days=nights)
    if check_out.year > year:
        continue

    stay_keys = {(room_id, (check_in + timedelta(days=i)).isoformat()) for i in range(nights)}
    if stay_keys & occupied:
        continue

    # Lead time realistically tied to channel
    lead = rand_lead(channel)
    book_date = check_in - timedelta(days=lead)
    if book_date.year < 2024:
        continue

    # ── Status assignment ──────────────────────────────────────────────────
    # Luxury guests cancel less (more committed). Agency bookings cancel more.
    cancel_prob = 0.12 if is_agency else (0.04 if segment == 'Luxury' else 0.07)
    if random.random() < cancel_prob:
        status   = 'cancelled'
        c_date   = (check_in - timedelta(days=random.randint(1, 30))).isoformat()
        c_reason = random.choice(CANCEL_REASONS)
    else:
        status   = 'completed' if check_out < TODAY else 'confirmed'
        c_date = c_reason = None
        occupied |= stay_keys

    guests = random.randint(1, max_guests)

    synthetic_bookings.append((
        customer_id, room_id,
        book_date.isoformat(), check_in.isoformat(), check_out.isoformat(),
        status, channel, guests,
        c_date, c_reason,
    ))
    generated += 1
    if generated % 5000 == 0:
        print(f"  ... {generated:,}/{TARGET:,} generated")

print(f"  ✓ {generated:,} synthetic bookings generated.")

# Sort chronologically so member tier evolves correctly
synthetic_bookings.sort(key=lambda b: (b[3], b[2]))   # check_in, then booking_date
print(f"  ✓ {len(synthetic_bookings):,} bookings sorted chronologically.")

# Insert. Triggers compute everything else.
print("  Inserting (this fires triggers per row, will take a few minutes)...")
INSERT_SQL = """
INSERT INTO Booking
(customer_id, room_id, booking_date, check_in, check_out,
 booking_status, booking_channel, guests,
 cancellation_date, cancellation_reason)
VALUES (?,?,?,?,?,?,?,?,?,?)
"""
inserted = 0
rejected = 0
for i in range(0, len(synthetic_bookings), BATCH):
    chunk = synthetic_bookings[i:i+BATCH]
    for row in chunk:
        try:
            cur.execute(INSERT_SQL, row)
            inserted += 1
        except sqlite3.IntegrityError:
            rejected += 1
    conn.commit()
    if (i + BATCH) % 10000 == 0 or i + BATCH >= len(synthetic_bookings):
        print(f"  ... {inserted:,} inserted ({rejected} rejected)")

print(f"  ✓ {inserted:,} bookings inserted, {rejected} rejected.")


# ============================================================
# FINAL SUMMARY
# ============================================================
print(f"\n✅ Data loading complete. Today is {TODAY}; status reflects this.")
for tbl in ['Brands','Hotel','Room','PriceModifier','RoomDay',
            'Customer','MeliaRewards','Booking','BookingModifier','Invoice']:
    n = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"   {tbl:<20} {n:>10,} rows")

print("\nBooking status breakdown:")
for status, n in cur.execute(
    "SELECT booking_status, COUNT(*) FROM Booking GROUP BY booking_status ORDER BY COUNT(*) DESC"
).fetchall():
    print(f"   {status:<12} {n:>10,}")

conn.close()
