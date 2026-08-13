import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import difflib
import pandas as pd
import requests
import streamlit as st
import extra_streamlit_components as stx
from github import Github
from duckduckgo_search import DDGS

# -----------------------------------------------------------------------------
# 1. PAGE CONFIG & MOBILE-RESPONSIVE FINDER STYLING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Home Inventory System",
    page_icon="🏠",
    layout="wide",
)

st.markdown(
    """
    <style>
    div[data-testid="stVerticalBlock"] > div {
        gap: 0.3rem !important;
    }
    [data-testid="stSidebarNav"] {display: none;}

    /* Mobile adjustments */
    @media (max-width: 768px) {
        .stColumns {
            flex-direction: column !important;
        }
        .finder-row {
            font-size: 0.85rem !important;
        }
    }

    .finder-header-btn button {
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        font-weight: 600 !important;
        color: #4a4a4a !important;
        font-size: 0.9rem !important;
        padding: 4px 8px !important;
        text-align: left !important;
        justify-content: flex-start !important;
    }
    .finder-header-btn button:hover {
        background-color: #e5e5e5 !important;
        border-radius: 4px !important;
    }

    .finder-row {
        padding: 6px 12px;
        border-bottom: 1px solid #f0f0f0;
        border-radius: 4px;
        transition: background-color 0.15s ease;
    }
    .finder-row:hover {
        background-color: #f4f6f8;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

IMAGE_DIR = "uploaded_images"
os.makedirs(IMAGE_DIR, exist_ok=True)

# API Keys & Secrets
OMDB_API_KEY = os.getenv("OMDB_KEY", "")
BGG_API_TOKEN = os.getenv("BGG_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
EMOJI_API_KEY = os.getenv("EMOJI_API_KEY", "")

DEFAULT_EMOJI_GRID = [
    "📦", "📁", "🧰", "🎬", "🎲", "🍳", "🛠️", "💻", "🎮", "📚",
    "👕", "🛋️", "📷", "🔒", "🏠", "🍕", "🚲", "🚗", "🎨", "👟"
]


# -----------------------------------------------------------------------------
# 2. MULTI-TIER WEB IMAGE SEARCH & DISPLAY HELPERS
# -----------------------------------------------------------------------------
def safe_st_image(img_path, width=None, use_container_width=False, default_emoji="📄"):
    """Safely renders st.image only if the path is a valid URL or an existing local file."""
    if not img_path or pd.isna(img_path):
        st.write(default_emoji)
        return

    path_str = str(img_path).strip()
    if not path_str:
        st.write(default_emoji)
        return

    is_url = path_str.startswith("http://") or path_str.startswith("https://")
    is_local_file = os.path.exists(path_str)

    if is_url or is_local_file:
        try:
            if width:
                st.image(path_str, width=width)
            else:
                st.image(path_str, use_container_width=use_container_width)
        except Exception:
            st.write(default_emoji)
    else:
        st.write(default_emoji)


@st.cache_data(ttl=3600)
def search_emojis_online(search_query):
    if not search_query or not search_query.strip():
        return DEFAULT_EMOJI_GRID

    if not EMOJI_API_KEY:
        return [em for em in DEFAULT_EMOJI_GRID if search_query.lower() in em] or DEFAULT_EMOJI_GRID

    try:
        q = urllib.parse.quote_plus(search_query.strip().lower())
        url = f"https://emoji-api.com/emojis?search={q}&access_key={EMOJI_API_KEY}"
        res = requests.get(url, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                return [item["character"] for item in data[:30] if "character" in item]
    except Exception:
        pass

    return DEFAULT_EMOJI_GRID


def search_multiple_web_images(query_text, num_results=8):
    """Searches web image sources using multi-tier fallback (DDG -> Simplified DDG -> Wikimedia) to prevent zero-result hits."""
    if not query_text or not str(query_text).strip():
        return []

    raw_query = str(query_text).strip()
    clean_q = re.sub(r"[^\w\s]", "", raw_query)
    results = []

    # TIER 1: DuckDuckGo Search (Cleaned Query)
    try:
        with DDGS() as ddgs:
            res = list(ddgs.images(clean_q, max_results=num_results))
            for r in res:
                img_url = r.get("image") or r.get("thumbnail")
                if img_url and img_url not in results:
                    results.append(img_url)
    except Exception:
        pass

    # TIER 2: DuckDuckGo Search (Simplified First 2 Words)
    if len(results) < 3 and len(clean_q.split()) > 2:
        short_q = " ".join(clean_q.split()[:2])
        try:
            with DDGS() as ddgs:
                res = list(ddgs.images(short_q, max_results=num_results))
                for r in res:
                    img_url = r.get("image") or r.get("thumbnail")
                    if img_url and img_url not in results:
                        results.append(img_url)
        except Exception:
            pass

    # TIER 3: Wikimedia Commons API Fallback
    if len(results) < 3:
        for search_term in [clean_q, " ".join(clean_q.split()[:2])]:
            try:
                encoded_q = urllib.parse.quote_plus(search_term)
                wiki_url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded_q}&gsrlimit=10&prop=pageimages&pithumbsize=500&format=json"
                res = requests.get(wiki_url, headers={"User-Agent": "HomeInventoryApp/1.0"}, timeout=5).json()
                pages = res.get("query", {}).get("pages", {})
                for _, page_data in pages.items():
                    thumb = page_data.get("thumbnail", {}).get("source")
                    if thumb and thumb not in results:
                        results.append(thumb)
            except Exception:
                pass

            if len(results) >= num_results:
                break

    return results[:num_results]


# -----------------------------------------------------------------------------
# 3. GITHUB SYNC & REMOTE IMAGE UPLOAD HELPERS
# -----------------------------------------------------------------------------
def push_csv_to_github(file_path, commit_message="Update inventory via app"):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        st.info("ℹ️ Local file saved.")
        return False

    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(GITHUB_REPO)

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            repo_file = repo.get_contents(file_path, ref="main")
            repo.update_file(
                path=file_path,
                message=commit_message,
                content=content,
                sha=repo_file.sha,
                branch="main"
            )
        except Exception:
            repo.create_file(
                path=file_path,
                message=commit_message,
                content=content,
                branch="main"
            )

        st.toast(f"✅ Synced `{file_path}` to GitHub repository!", icon="🚀")
        return True
    except Exception as e:
        st.error(f"GitHub Sync Error: {e}")
        return False


def push_image_to_github(uploaded_file):
    """Uploads a binary image file to GitHub repo and returns its public raw CDN URL."""
    filename = "".join(c for c in uploaded_file.name if c.isalnum() or c in "._-")
    github_path = f"{IMAGE_DIR}/{filename}"

    if not GITHUB_TOKEN or not GITHUB_REPO:
        local_path = os.path.join(IMAGE_DIR, filename)
        with open(local_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        return local_path

    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(GITHUB_REPO)
        file_bytes = uploaded_file.getvalue()

        try:
            existing_file = repo.get_contents(github_path, ref="main")
            repo.update_file(
                path=github_path,
                message=f"Update image {filename}",
                content=file_bytes,
                sha=existing_file.sha,
                branch="main"
            )
        except Exception:
            repo.create_file(
                path=github_path,
                message=f"Upload image {filename}",
                content=file_bytes,
                branch="main"
            )

        raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{github_path}"
        return raw_url
    except Exception as e:
        st.error(f"Failed to upload image to GitHub: {e}")
        local_path = os.path.join(IMAGE_DIR, filename)
        with open(local_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        return local_path


# -----------------------------------------------------------------------------
# 4. DATA & API HELPERS (OMDb & BGG INTEGRATION)
# -----------------------------------------------------------------------------
CUSTOM_CATEGORIES_FILE = "custom_categories_registry.csv"

def safe_load_csv(file_path, expected_columns):
    if not os.path.exists(file_path):
        df = pd.DataFrame(columns=expected_columns)
        return df.astype(object)
    try:
        df = pd.read_csv(file_path, on_bad_lines="skip")
        for col in expected_columns:
            if col not in df.columns:
                df[col] = ""
        return df.astype(object)
    except Exception as e:
        st.error(f"Error reading {file_path}: {e}")
        return pd.DataFrame(columns=expected_columns).astype(object)


def load_custom_categories():
    if not os.path.exists(CUSTOM_CATEGORIES_FILE):
        return []
    try:
        df = pd.read_csv(CUSTOM_CATEGORIES_FILE)
        categories = []
        for _, row in df.iterrows():
            fields = [f.strip() for f in str(row["Fields"]).split(",") if f.strip()]
            categories.append({
                "Name": str(row["Category Name"]),
                "Icon": str(row["Icon"]),
                "File": str(row["File Path"]),
                "Primary_Col": str(row["Primary Col"]),
                "Fields": fields,
            })
        return categories
    except Exception as e:
        st.error(f"Error reading categories registry: {e}")
        return []


def save_custom_category(cat_name, icon, primary_col, fields_list):
    clean_filename = "".join(c for c in cat_name.lower().replace(" ", "_") if c.isalnum() or c == "_") + "_collection.csv"
    
    if "Image_Path" not in fields_list:
        fields_list.append("Image_Path")
    if primary_col not in fields_list:
        fields_list.insert(0, primary_col)

    empty_df = pd.DataFrame(columns=fields_list)
    empty_df.to_csv(clean_filename, index=False)
    push_csv_to_github(clean_filename, f"Create database for new category '{cat_name}'")

    reg_df = safe_load_csv(CUSTOM_CATEGORIES_FILE, ["Category Name", "Icon", "File Path", "Primary Col", "Fields"])
    new_reg = {
        "Category Name": cat_name,
        "Icon": icon,
        "File Path": clean_filename,
        "Primary Col": primary_col,
        "Fields": ",".join(fields_list)
    }
    updated_reg = pd.concat([reg_df, pd.DataFrame([new_reg])], ignore_index=True)
    updated_reg.to_csv(CUSTOM_CATEGORIES_FILE, index=False)
    push_csv_to_github(CUSTOM_CATEGORIES_FILE, f"Register category '{cat_name}'")


# OMDB Helper Functions
def fetch_omdb_movie_matches(movie_title):
    if not OMDB_API_KEY or not movie_title or not str(movie_title).strip():
        return []

    try:
        encoded_q = urllib.parse.quote_plus(str(movie_title).strip())
        url = f"http://www.omdbapi.com/?s={encoded_q}&apikey={OMDB_API_KEY}"
        res = requests.get(url, timeout=5).json()

        if res.get("Response") == "True":
            results = res.get("Search", [])
            matches = []
            for item in results[:6]:
                d_url = f"http://www.omdbapi.com/?i={item['imdbID']}&apikey={OMDB_API_KEY}"
                d_res = requests.get(d_url, timeout=4).json()
                if d_res.get("Response") == "True":
                    matches.append({
                        "Title": d_res.get("Title", ""),
                        "Year Released": d_res.get("Year", ""),
                        "Rating": d_res.get("Rated", ""),
                        "Length of Movie": d_res.get("Runtime", ""),
                        "Type": d_res.get("Type", "movie").capitalize(),
                        "Genre": d_res.get("Genre", ""),
                        "Image_Path": d_res.get("Poster", "") if d_res.get("Poster") != "N/A" else ""
                    })
            return matches
    except Exception as e:
        st.error(f"OMDb Search Error: {e}")
    return []


def fetch_collection_movies(collection_title):
    return fetch_omdb_movie_matches(collection_title)


# BGG Helper Functions
@st.cache_data(ttl=86400)
def fetch_bgg_game_matches(game_title):
    if not game_title or not game_title.strip():
        return []

    try:
        encoded_q = urllib.parse.quote_plus(game_title.strip())
        url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame"
        headers = {
            "User-Agent": "HomeInventoryApp/1.0 (Python Streamlit Inventory Tool)",
            "Accept": "text/xml,application/xml"
        }
        if BGG_API_TOKEN:
            headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"

        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = []
            for item in root.findall("item")[:8]:
                bgg_id = item.attrib.get("id")
                name_elem = item.find("name")
                name = name_elem.attrib.get("value") if name_elem is not None else game_title
                year_elem = item.find("yearpublished")
                year = year_elem.attrib.get("value") if year_elem is not None else ""
                items.append({"id": bgg_id, "name": name, "year": year})
            return items
    except Exception as e:
        st.error(f"BGG Fetch Error: {e}")
    return []


@st.cache_data(ttl=86400)
def fetch_bgg_game_details(bgg_id, max_retries=3):
    if not bgg_id:
        return {}

    url = f"https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}"
    headers = {
        "User-Agent": "HomeInventoryApp/1.0 (Python Streamlit Inventory Tool)",
        "Accept": "text/xml,application/xml"
    }
    if BGG_API_TOKEN:
        headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"

    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 202:
                time.sleep(2 * (attempt + 1))
                continue

            if res.status_code == 200:
                root = ET.fromstring(res.content)
                item = root.find("item")
                if item is not None:
                    image_elem = item.find("image")
                    if image_elem is None or not image_elem.text:
                        image_elem = item.find("thumbnail")
                    image_url = image_elem.text if image_elem is not None else ""

                    min_p = item.find("minplayers").attrib.get("value") if item.find("minplayers") is not None else ""
                    max_p = item.find("maxplayers").attrib.get("value") if item.find("maxplayers") is not None else ""
                    players = f"{min_p}-{max_p} Players" if min_p and max_p and min_p != max_p else f"{min_p} Players"

                    min_t = item.find("minplaytime").attrib.get("value") if item.find("minplaytime") is not None else ""
                    max_t = item.find("maxplaytime").attrib.get("value") if item.find("maxplaytime") is not None else ""
                    length = f"{min_t}-{max_t} min" if min_t and max_t and min_t != max_t else f"{min_t} min"

                    age = item.find("minage").attrib.get("value") if item.find("minage") is not None else ""
                    if age and age != "0":
                        age = f"{age}+"

                    return {
                        "Image_Path": image_url,
                        "Number of Players": players,
                        "Length of Play": length,
                        "Age Rating": age,
                    }
        except Exception as e:
            st.error(f"BGG Detail Fetch Error: {e}")
            break

    return {}


def save_multiple_movies_to_csv(file_path, movies_list):
    expected_cols = ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"]
    existing_df = safe_load_csv(file_path, expected_cols)
    new_df = pd.DataFrame(movies_list).astype(object)
    
    existing_titles = set(existing_df["Title"].astype(str).str.lower().str.strip())
    new_df = new_df[~new_df["Title"].astype(str).str.lower().str.strip().isin(existing_titles)]
    
    if new_df.empty:
        st.warning("All titles in this collection already exist in your inventory!")
        return False

    updated_df = pd.concat([existing_df, new_df], ignore_index=True)
    updated_df.to_csv(file_path, index=False)
    push_csv_to_github(file_path, f"Add collection ({len(new_df)} movies)")
    return True


def save_edited_row(file_path, original_title_or_name, updated_row_dict, key_col):
    df = pd.read_csv(file_path, on_bad_lines="skip").astype(object)

    mask = (
        df[key_col]
        .astype(str)
        .str.lower()
        .str.strip()
        == str(original_title_or_name).lower().strip()
    )
    if not mask.any():
        return False

    idx = df[mask].index[0]

    if updated_row_dict.get("_DELETE_"):
        df = df.drop(idx).reset_index(drop=True)
        msg = f"Delete item '{original_title_or_name}'"
    else:
        for k, v in updated_row_dict.items():
            if k in df.columns:
                df.at[idx, k] = str(v)
        msg = f"Edit item '{original_title_or_name}'"

    df.to_csv(file_path, index=False)
    push_csv_to_github(file_path, msg)
    return True


# -----------------------------------------------------------------------------
# 5. AUTHENTICATION & 30-DAY PERSISTENT COOKIE
# -----------------------------------------------------------------------------
PIN_CODE = "1234"

def get_cookie_manager():
    if "cookie_manager" not in st.session_state:
        st.session_state["cookie_manager"] = stx.CookieManager()
    return st.session_state["cookie_manager"]


def check_password():
    cookie_manager = get_cookie_manager()

    auth_cookie = cookie_manager.get("home_inventory_auth_token")

    if auth_cookie == "logged_in_30_days_valid":
        st.session_state["authenticated"] = True
        return True

    if not st.session_state.get("authenticated", False):
        st.title("🏠 Home Inventory Access")
        input_pin = st.text_input("Enter Invite Code / PIN:", type="password", key="pin_input")

        if st.button("Login"):
            if input_pin == PIN_CODE:
                st.session_state["authenticated"] = True
                cookie_manager.set(
                    "home_inventory_auth_token",
                    "logged_in_30_days_valid",
                    key="set_auth_cookie",
                    max_age=30 * 86400  # 30 Days in seconds
                )
                time.sleep(0.2)
                st.rerun()
            else:
                st.error("Invalid invite code.")
        return False

    return True


def logout_user():
    cookie_manager = get_cookie_manager()
    cookie_manager.delete("home_inventory_auth_token", key="delete_auth_cookie")
    st.session_state["authenticated"] = False
    time.sleep(0.2)
    st.rerun()


if check_password():
    custom_cats = load_custom_categories()

    if "bulk_movie_scan_results" not in st.session_state:
        st.session_state["bulk_movie_scan_results"] = None
    if "bulk_game_scan_results" not in st.session_state:
        st.session_state["bulk_game_scan_results"] = None
    if "bulk_kitchen_scan_results" not in st.session_state:
        st.session_state["bulk_kitchen_scan_results"] = None

    for i, c_cat in enumerate(custom_cats):
        key_name = f"bulk_cust_scan_results_{i}"
        if key_name not in st.session_state:
            st.session_state[key_name] = None

    # -----------------------------------------------------------------------------
    # 6. FINDER TOP TOOLBAR
    # -----------------------------------------------------------------------------
    if "finder_sort_col" not in st.session_state:
        st.session_state["finder_sort_col"] = "Name"
    if "finder_sort_asc" not in st.session_state:
        st.session_state["finder_sort_asc"] = True
    if "finder_view_mode" not in st.session_state:
        st.session_state["finder_view_mode"] = "List"

    toolbar_col1, toolbar_col2, toolbar_col3 = st.columns([5, 3.5, 1.5])

    with toolbar_col1:
        st.markdown("### 📁 Desktop / Home Inventory")

    with toolbar_col2:
        v1, v2, v3, v4 = st.columns(4)
        if v1.button("⊞ Icons", use_container_width=True):
            st.session_state["finder_view_mode"] = "Icons"
            st.rerun()
        if v2.button("☰ List", use_container_width=True):
            st.session_state["finder_view_mode"] = "List"
            st.rerun()
        if v3.button("|| Columns", use_container_width=True):
            st.session_state["finder_view_mode"] = "Columns"
            st.rerun()
        if v4.button("➕ Add", use_container_width=True):
            st.session_state["show_add_form"] = not st.session_state.get("show_add_form", False)
            st.rerun()

    with toolbar_col3:
        if st.button("🚪 Log Out", use_container_width=True):
            logout_user()

    st.markdown("---")

    # -----------------------------------------------------------------------------
    # 7. ADD NEW ITEM DRAWER
    # -----------------------------------------------------------------------------
    if st.session_state.get("show_add_form", False):
        with st.container(border=True):
            st.subheader("➕ Add New Inventory Item")
            
            category_options = ["Movies & TV", "Board & Card Games", "Kitchen Gear"] + [c["Name"] for c in custom_cats]
            category = st.selectbox("Select Item Category", category_options)

            if category == "Movies & TV":
                with st.form("movie_form", clear_on_submit=True):
                    title = st.text_input("Title *")
                    rating = st.text_input("Rating")
                    year = st.text_input("Year Released")
                    length = st.text_input("Length of Movie")
                    m_type = st.selectbox("Type", ["Movie", "TV", "Collection / Pack"])
                    genre = st.text_input("Genre")
                    poster_link = st.text_input("Poster URL")
                    uploaded_image = st.file_uploader("Or Upload Custom Poster File", type=["jpg", "png", "jpeg"], key="add_m_file")

                    if st.form_submit_button("Save Movie Entry"):
                        if title:
                            final_img = poster_link
                            if uploaded_image:
                                final_img = push_image_to_github(uploaded_image)

                            new_entry = {"Title": title, "Rating": rating, "Year Released": year, "Length of Movie": length, "Type": m_type, "Genre": genre, "Image_Path": final_img}
                            df = safe_load_csv("movies_and_tv_collection.csv", list(new_entry.keys()))
                            pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv("movies_and_tv_collection.csv", index=False)
                            push_csv_to_github("movies_and_tv_collection.csv", f"Add movie '{title}'")
                            st.session_state["show_add_form"] = False
                            st.rerun()

            elif category == "Board & Card Games":
                with st.form("game_form", clear_on_submit=True):
                    title = st.text_input("Game Title *")
                    players = st.text_input("Number of Players")
                    length = st.text_input("Length of Play")
                    age = st.text_input("Age Rating")
                    box_url = st.text_input("Box Photo URL")
                    uploaded_image = st.file_uploader("Or Upload Custom Box Art File", type=["jpg", "png", "jpeg"], key="add_g_file")

                    if st.form_submit_button("Save Game"):
                        if title:
                            final_img = box_url
                            if uploaded_image:
                                final_img = push_image_to_github(uploaded_image)

                            new_entry = {"Title": title, "Number of Players": players, "Length of Play": length, "Age Rating": age, "Style of Game": "Board", "Image_Path": final_img}
                            df = safe_load_csv("board_and_card_games_collection.csv", list(new_entry.keys()))
                            pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv("board_and_card_games_collection.csv", index=False)
                            push_csv_to_github("board_and_card_games_collection.csv", f"Add game '{title}'")
                            st.session_state["show_add_form"] = False
                            st.rerun()

            elif category == "Kitchen Gear":
                with st.form("kitchen_form", clear_on_submit=True):
                    title = st.text_input("Name of Item *")
                    eq_type = st.selectbox("Type of Equipment", ["Appliance", "Cookware", "Utensil", "Decoration"])
                    manual = st.text_input("Manual Link URL")
                    image_url = st.text_input("Photo Image URL")
                    uploaded_image = st.file_uploader("Or Upload Custom Photo File", type=["jpg", "png", "jpeg"], key="add_k_file")

                    if st.form_submit_button("Save Kitchen / Decor Item"):
                        if title:
                            final_img = image_url
                            if uploaded_image:
                                final_img = push_image_to_github(uploaded_image)

                            new_entry = {"Name of Item": title, "Type of Equipment": eq_type, "Instruction Manual Link": manual, "Image_Path": final_img}
                            df = safe_load_csv("kitchen_gear_inventory_v2.csv", list(new_entry.keys()))
                            pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv("kitchen_gear_inventory_v2.csv", index=False)
                            push_csv_to_github("kitchen_gear_inventory_v2.csv", f"Add kitchen item '{title}'")
                            st.session_state["show_add_form"] = False
                            st.rerun()

            else:
                target_cat = next(c for c in custom_cats if c["Name"] == category)
                with st.form(f"custom_form_{target_cat['Name']}", clear_on_submit=True):
                    custom_data = {}
                    for field in target_cat["Fields"]:
                        if field != "Image_Path":
                            custom_data[field] = st.text_input(f"{field} * " if field == target_cat["Primary_Col"] else f"{field}")
                    
                    cust_img_url = st.text_input("Photo / Image URL")
                    cust_file = st.file_uploader("Or Upload Image File", type=["jpg", "png", "jpeg"], key=f"add_custom_{target_cat['Name']}")

                    if st.form_submit_button(f"Save to {target_cat['Name']}"):
                        primary_val = custom_data.get(target_cat["Primary_Col"])
                        if primary_val:
                            final_img = cust_img_url
                            if cust_file:
                                final_img = push_image_to_github(cust_file)
                            
                            custom_data["Image_Path"] = final_img
                            df = safe_load_csv(target_cat["File"], target_cat["Fields"])
                            pd.concat([df, pd.DataFrame([custom_data])], ignore_index=True).to_csv(target_cat["File"], index=False)
                            push_csv_to_github(target_cat["File"], f"Add {category} item '{primary_val}'")
                            st.session_state["show_add_form"] = False
                            st.rerun()

        st.markdown("---")

    # -----------------------------------------------------------------------------
    # 8. LOAD DATABASES & MERGE
    # -----------------------------------------------------------------------------
    df_movies = safe_load_csv("movies_and_tv_collection.csv", ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
    df_games = safe_load_csv("board_and_card_games_collection.csv", ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
    df_kitchen = safe_load_csv("kitchen_gear_inventory_v2.csv", ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"])

    m_df = df_movies.copy()
    m_df["Name"] = m_df["Title"]
    m_df["Category"] = "Movies & TV"
    m_df["Kind"] = m_df["Type"].fillna("Movie")
    m_df["Details"] = "Year: " + m_df["Year Released"].astype(str) + " | " + m_df["Genre"].astype(str)
    m_df["_File"] = "movies_and_tv_collection.csv"
    m_df["_TitleCol"] = "Title"
    m_df["_Cols"] = [["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"]] * len(m_df)

    g_df = df_games.copy()
    g_df["Name"] = g_df["Title"]
    g_df["Category"] = "Board & Card Games"
    g_df["Kind"] = g_df["Style of Game"].fillna("Game")
    g_df["Details"] = "Players: " + g_df["Number of Players"].astype(str) + " | " + g_df["Length of Play"].astype(str)
    g_df["_File"] = "board_and_card_games_collection.csv"
    g_df["_TitleCol"] = "Title"
    g_df["_Cols"] = [["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"]] * len(g_df)

    k_df = df_kitchen.copy()
    k_df["Name"] = k_df["Name of Item"]
    k_df["Category"] = "Kitchen Gear"
    k_df["Kind"] = k_df["Type of Equipment"].fillna("Kitchen")
    k_df["Details"] = "Equipment: " + k_df["Type of Equipment"].astype(str)
    k_df["_File"] = "kitchen_gear_inventory_v2.csv"
    k_df["_TitleCol"] = "Name of Item"
    k_df["_Cols"] = [["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"]] * len(k_df)

    custom_dfs = []
    for c in custom_cats:
        c_loaded = safe_load_csv(c["File"], c["Fields"])
        c_copy = c_loaded.copy()
        c_copy["Name"] = c_copy[c["Primary_Col"]]
        c_copy["Category"] = c["Name"]
        c_copy["Kind"] = c["Name"]
        c_copy["Details"] = "Custom Item"
        c_copy["_File"] = c["File"]
        c_copy["_TitleCol"] = c["Primary_Col"]
        c_copy["_Cols"] = [c["Fields"]] * len(c_copy)
        custom_dfs.append(c_copy)

    master_df = pd.concat([m_df, g_df, k_df] + custom_dfs, ignore_index=True)

    # -----------------------------------------------------------------------------
    # 9. GLOBAL FUZZY SEARCH BAR & FINDER TABS
    # -----------------------------------------------------------------------------
    finder_search_q = st.text_input("🔍 Search Desktop Files (fuzzy & typo matching)...", key="finder_search_q")

    if finder_search_q:
        query = finder_search_q.strip().lower()

        def is_fuzzy_match(name):
            name_str = str(name).lower()
            if query in name_str:
                return True
            if any(q_word in name_str for q_word in query.split()):
                return True
            similarity = difflib.SequenceMatcher(None, query, name_str).ratio()
            return similarity >= 0.50

        mask = master_df["Name"].apply(is_fuzzy_match)
        master_df = master_df[mask]

    tab_names = ["🌐 All Files (Master)", "🎬 Movies & TV", "🎲 Board & Card Games", "🍳 Kitchen Gear"] + [f"{c['Icon']} {c['Name']}" for c in custom_cats] + ["➕ Add Category"]
    tabs = st.tabs(tab_names)

    def render_edit_drawer(unique_key_id, item_id, row, editable_cols, file_path, title_col):
        edit_inputs = {}
        for col_name in editable_cols:
            input_key = f"edit_{unique_key_id}_{col_name}"
            if input_key not in st.session_state:
                st.session_state[input_key] = str(row.get(col_name, ""))
            edit_inputs[col_name] = st.text_input(f"{col_name}", key=input_key)

        uploaded_img_file = st.file_uploader(
            f"Upload Image File for {item_id}", type=["jpg", "png", "jpeg"], key=f"upload_{unique_key_id}"
        )

        category_type = str(row.get("Category", ""))

        # Category-Specific API Integration Logic
        if category_type == "Movies & TV":
            st.markdown("---")
            st.caption("🎬 Search OMDb Database for Metadata & Poster:")
            omdb_q_input = st.text_input("OMDb Movie Search Terms", value=item_id, key=f"omdb_q_{unique_key_id}")

            if st.button("🔍 Search OMDb", key=f"btn_search_omdb_{unique_key_id}"):
                found_omdb = fetch_omdb_movie_matches(omdb_q_input)
                st.session_state[f"edit_omdb_results_{unique_key_id}"] = found_omdb

            if st.session_state.get(f"edit_omdb_results_{unique_key_id}"):
                omdb_results = st.session_state[f"edit_omdb_results_{unique_key_id}"]
                if not omdb_results:
                    st.info("No OMDb matches found for this query.")
                else:
                    st.markdown("##### Select a Matching Movie:")
                    grid_cols = st.columns(min(len(omdb_results), 4))
                    for idx_m, m_item in enumerate(omdb_results):
                        with grid_cols[idx_m % 4]:
                            with st.container(border=True):
                                safe_st_image(m_item["Image_Path"], use_container_width=True)
                                st.markdown(f"**{m_item['Title']}** ({m_item['Year Released']})")
                                st.caption(f"Rated: {m_item['Rating']} | {m_item['Genre']}")
                                if st.button("✅ Accept & Apply Metadata", key=f"btn_apply_omdb_{unique_key_id}_{idx_m}"):
                                    for k, v in m_item.items():
                                        if k in edit_inputs:
                                            edit_inputs[k] = v
                                    if save_edited_row(file_path, item_id, edit_inputs, title_col):
                                        st.session_state[f"expand_edit_{unique_key_id}"] = False
                                        st.session_state.pop(f"edit_omdb_results_{unique_key_id}", None)
                                        st.success(f"Updated metadata for '{item_id}'!")
                                        st.rerun()

        elif category_type == "Board & Card Games":
            st.markdown("---")
            st.caption("🎲 Search BoardGameGeek Database for Details & Box Art:")
            bgg_q_input = st.text_input("BGG Game Search Terms", value=item_id, key=f"bgg_q_{unique_key_id}")

            if st.button("🔍 Search BGG", key=f"btn_search_bgg_{unique_key_id}"):
                bgg_matches = fetch_bgg_game_matches(bgg_q_input)
                detailed_bgg = []
                for match in bgg_matches:
                    details = fetch_bgg_game_details(match["id"])
                    detailed_bgg.append({
                        "Title": match["name"],
                        "Year": match["year"],
                        **details
                    })
                st.session_state[f"edit_bgg_results_{unique_key_id}"] = detailed_bgg

            if st.session_state.get(f"edit_bgg_results_{unique_key_id}"):
                bgg_results = st.session_state[f"edit_bgg_results_{unique_key_id}"]
                if not bgg_results:
                    st.info("No BGG matches found for this query.")
                else:
                    st.markdown("##### Select a Matching Game:")
                    grid_cols = st.columns(min(len(bgg_results), 4))
                    for idx_g, g_item in enumerate(bgg_results):
                        with grid_cols[idx_g % 4]:
                            with st.container(border=True):
                                safe_st_image(g_item.get("Image_Path", ""), use_container_width=True)
                                st.markdown(f"**{g_item['Title']}** ({g_item.get('Year', '')})")
                                st.caption(f"{g_item.get('Number of Players', '')} | {g_item.get('Length of Play', '')}")
                                if st.button("✅ Accept & Apply BGG Details", key=f"btn_apply_bgg_{unique_key_id}_{idx_g}"):
                                    if "Title" in edit_inputs:
                                        edit_inputs["Title"] = g_item["Title"]
                                    if "Number of Players" in edit_inputs and g_item.get("Number of Players"):
                                        edit_inputs["Number of Players"] = g_item["Number of Players"]
                                    if "Length of Play" in edit_inputs and g_item.get("Length of Play"):
                                        edit_inputs["Length of Play"] = g_item["Length of Play"]
                                    if "Age Rating" in edit_inputs and g_item.get("Age Rating"):
                                        edit_inputs["Age Rating"] = g_item["Age Rating"]
                                    if "Image_Path" in edit_inputs and g_item.get("Image_Path"):
                                        edit_inputs["Image_Path"] = g_item["Image_Path"]

                                    if save_edited_row(file_path, item_id, edit_inputs, title_col):
                                        st.session_state[f"expand_edit_{unique_key_id}"] = False
                                        st.session_state.pop(f"edit_bgg_results_{unique_key_id}", None)
                                        st.success(f"Updated details for '{item_id}'!")
                                        st.rerun()

        else:
            # General Web Image Search Fallback for Kitchen Gear and Custom Categories
            st.markdown("---")
            st.caption("🌐 Search Web Product Images (Returns Close Matches):")
            search_query_input = st.text_input("Web Search Terms", value=item_id, key=f"web_q_{unique_key_id}")

            if st.button("🔍 Search Web Photos", key=f"btn_search_web_{unique_key_id}"):
                found_imgs = search_multiple_web_images(search_query_input, num_results=8)
                st.session_state[f"edit_search_results_{unique_key_id}"] = found_imgs

            if st.session_state.get(f"edit_search_results_{unique_key_id}"):
                c_results = st.session_state[f"edit_search_results_{unique_key_id}"]
                if not c_results:
                    st.info("No close web photos found for this query.")
                else:
                    st.markdown("##### Select a Matching Image Option:")
                    grid_cols = st.columns(4)
                    for idx_img, img_url in enumerate(c_results):
                        with grid_cols[idx_img % 4]:
                            with st.container(border=True):
                                safe_st_image(img_url, use_container_width=True)
                                if st.button("✅ Pick This Image", key=f"btn_apply_img_{unique_key_id}_{idx_img}"):
                                    edit_inputs["Image_Path"] = img_url
                                    if save_edited_row(file_path, item_id, edit_inputs, title_col):
                                        st.session_state[f"expand_edit_{unique_key_id}"] = False
                                        st.session_state.pop(f"edit_search_results_{unique_key_id}", None)
                                        st.success(f"Saved image for '{item_id}'!")
                                        st.rerun()

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Changes", key=f"save_{unique_key_id}"):
                if uploaded_img_file:
                    remote_img_url = push_image_to_github(uploaded_img_file)
                    edit_inputs["Image_Path"] = remote_img_url

                if save_edited_row(file_path, item_id, edit_inputs, title_col):
                    st.session_state[f"expand_edit_{unique_key_id}"] = False
                    st.rerun()

        with col2:
            if st.button("🗑️ Delete File", key=f"del_{unique_key_id}"):
                if save_edited_row(file_path, item_id, {"_DELETE_": True}, title_col):
                    st.session_state[f"expand_edit_{unique_key_id}"] = False
                    st.rerun()

    def display_finder_view(df_subset, tab_key_prefix):
        if df_subset.empty:
            st.info("Folder is empty or no files matched your search.")
            return

        sort_col = st.session_state["finder_sort_col"]
        sort_asc = st.session_state["finder_sort_asc"]

        if sort_col in df_subset.columns:
            df_subset = df_subset.sort_values(
                by=sort_col,
                ascending=sort_asc,
                key=lambda x: x.astype(str).str.lower()
            )

        df_subset = df_subset.reset_index(drop=True)

        if st.session_state["finder_view_mode"] in ["List", "Columns"]:
            h1, h2, h3, h4, h5 = st.columns([0.4, 3.5, 1.8, 1.8, 0.8])

            def make_header_arrow(col_name):
                if st.session_state["finder_sort_col"] == col_name:
                    return " ▲" if st.session_state["finder_sort_asc"] else " ▼"
                return ""

            with h1:
                st.caption("Icon")
            with h2:
                if st.button(f"Name{make_header_arrow('Name')}", key=f"hdr_name_{tab_key_prefix}"):
                    if st.session_state["finder_sort_col"] == "Name":
                        st.session_state["finder_sort_asc"] = not st.session_state["finder_sort_asc"]
                    else:
                        st.session_state["finder_sort_col"] = "Name"
                        st.session_state["finder_sort_asc"] = True
                    st.rerun()
            with h3:
                if st.button(f"Category{make_header_arrow('Category')}", key=f"hdr_cat_{tab_key_prefix}"):
                    if st.session_state["finder_sort_col"] == "Category":
                        st.session_state["finder_sort_asc"] = not st.session_state["finder_sort_asc"]
                    else:
                        st.session_state["finder_sort_col"] = "Category"
                        st.session_state["finder_sort_asc"] = True
                    st.rerun()
            with h4:
                if st.button(f"Kind{make_header_arrow('Kind')}", key=f"hdr_kind_{tab_key_prefix}"):
                    if st.session_state["finder_sort_col"] == "Kind":
                        st.session_state["finder_sort_asc"] = not st.session_state["finder_sort_asc"]
                    else:
                        st.session_state["finder_sort_col"] = "Kind"
                        st.session_state["finder_sort_asc"] = True
                    st.rerun()
            with h5:
                st.caption("Action")

            st.markdown("<hr style='margin: 0 0 8px 0; border-color: #d0d0d0;' />", unsafe_allow_html=True)

            for idx, row in df_subset.iterrows():
                item_name = str(row["Name"])
                cat = str(row["Category"])
                kind = str(row["Kind"])
                file_path = str(row["_File"])
                title_col = str(row["_TitleCol"])
                editable_cols = row["_Cols"]
                unique_key_id = f"{tab_key_prefix}_{idx}_{hash(item_name)}"

                c1, c2, c3, c4, c5 = st.columns([0.4, 3.5, 1.8, 1.8, 0.8], vertical_alignment="center")

                with c1:
                    img_val = row.get("Image_Path", "")
                    safe_st_image(img_val, width=24, default_emoji="📄")

                with c2:
                    st.markdown(f"**{item_name}**")
                with c3:
                    st.caption(cat)
                with c4:
                    st.caption(kind)
                with c5:
                    exp_key = f"expand_edit_{unique_key_id}"
                    if exp_key not in st.session_state:
                        st.session_state[exp_key] = False

                    if st.button("✏️ Edit", key=f"btn_edit_{unique_key_id}"):
                        st.session_state[exp_key] = not st.session_state[exp_key]

                if st.session_state.get(exp_key, False):
                    st.markdown("---")
                    st.subheader(f"✏️ Editing File: {item_name}")
                    render_edit_drawer(unique_key_id, item_name, row, editable_cols, file_path, title_col)

        else:
            cols = st.columns(4)
            for idx, row in df_subset.iterrows():
                col = cols[idx % 4]
                item_name = str(row["Name"])
                cat = str(row["Category"])
                file_path = str(row["_File"])
                title_col = str(row["_TitleCol"])
                editable_cols = row["_Cols"]
                unique_key_id = f"icon_{tab_key_prefix}_{idx}_{hash(item_name)}"

                with col:
                    with st.container(border=True):
                        img_val = row.get("Image_Path", "")
                        safe_st_image(img_val, use_container_width=True, default_emoji="📁")

                        st.markdown(f"**{item_name}**")
                        st.caption(cat)

                        with st.expander("✏️ Open / Edit"):
                            render_edit_drawer(unique_key_id, item_name, row, editable_cols, file_path, title_col)

    # -----------------------------------------------------------------------------
    # 10. TAB RENDERING
    # -----------------------------------------------------------------------------
    with tabs[0]:
        display_finder_view(master_df, "master")

    # MOVIES TAB
    with tabs[1]:
        with st.expander("🛠️ Bulk Movie Metadata & Poster Scanner"):
            missing_m_mask = (
                df_movies["Image_Path"].isna()
                | (df_movies["Image_Path"].astype(str).str.strip() == "")
                | df_movies["Rating"].isna()
                | (df_movies["Rating"].astype(str).str.strip() == "")
            )
            missing_m_df = df_movies[missing_m_mask]

            if missing_m_df.empty:
                st.success("🎉 All titles in your Movies database have complete metadata and posters!")
            else:
                st.warning(f"Found {len(missing_m_df)} movie(s) missing metadata or posters.")
                if st.button("🌐 Scan OMDb for Missing Movie Metadata", key="btn_omdb_bulk_scan"):
                    movie_scan_results = []
                    progress_bar = st.progress(0)

                    for i, (_, m_row) in enumerate(missing_m_df.iterrows()):
                        m_title = m_row["Title"]
                        matches = fetch_omdb_movie_matches(m_title)
                        if matches:
                            movie_scan_results.append({
                                "Original_Title": m_title,
                                "Match": matches[0]
                            })
                        progress_bar.progress((i + 1) / len(missing_m_df))

                    st.session_state["bulk_movie_scan_results"] = movie_scan_results
                    st.rerun()

                if st.session_state.get("bulk_movie_scan_results") is not None:
                    st.markdown("#### Review Discovered OMDb Metadata")
                    m_results = st.session_state["bulk_movie_scan_results"]

                    if not m_results:
                        st.info("No matching metadata was found on OMDb.")
                    else:
                        if st.button("⚡ Accept All OMDb Metadata Updates", key="btn_accept_omdb_bulk"):
                            m_df_csv = safe_load_csv("movies_and_tv_collection.csv", ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
                            for item in m_results:
                                orig = item["Original_Title"]
                                match = item["Match"]
                                mask_m = m_df_csv["Title"].astype(str).str.lower().str.strip() == str(orig).lower().strip()
                                if mask_m.any():
                                    idx = m_df_csv[mask_m].index[0]
                                    for key_name, val in match.items():
                                        if key_name in m_df_csv.columns and val:
                                            m_df_csv.at[idx, key_name] = str(val)
                            m_df_csv.to_csv("movies_and_tv_collection.csv", index=False)
                            push_csv_to_github("movies_and_tv_collection.csv", "Bulk OMDb metadata update")
                            st.session_state["bulk_movie_scan_results"] = None
                            st.success("Updated movie metadata from OMDb!")
                            st.rerun()

                        st.markdown("---")
                        m_cols = st.columns(3)
                        for idx_m_res, m_item in enumerate(m_results):
                            with m_cols[idx_m_res % 3]:
                                with st.container(border=True):
                                    match_data = m_item["Match"]
                                    safe_st_image(match_data["Image_Path"], use_container_width=True)
                                    st.markdown(f"**{match_data['Title']}** ({match_data['Year Released']})")
                                    st.caption(f"Rated: {match_data['Rating']} | {match_data['Genre']}")
                                    if st.button("✅ Accept Item", key=f"btn_accept_single_m_{idx_m_res}"):
                                        m_df_csv = safe_load_csv("movies_and_tv_collection.csv", ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
                                        orig = m_item["Original_Title"]
                                        mask_m = m_df_csv["Title"].astype(str).str.lower().str.strip() == str(orig).lower().strip()
                                        if mask_m.any():
                                            idx = m_df_csv[mask_m].index[0]
                                            for key_name, val in match_data.items():
                                                if key_name in m_df_csv.columns and val:
                                                    m_df_csv.at[idx, key_name] = str(val)
                                            m_df_csv.to_csv("movies_and_tv_collection.csv", index=False)
                                            push_csv_to_github("movies_and_tv_collection.csv", f"Add metadata for {orig}")
                                        st.session_state["bulk_movie_scan_results"].pop(idx_m_res)
                                        st.rerun()

        st.markdown("---")
        display_finder_view(master_df[master_df["Category"] == "Movies & TV"], "movies")

    # BOARD GAMES TAB
    with tabs[2]:
        with st.expander("🛠️ Bulk Game Details & Box Art Web Scanner"):
            missing_games_mask = (
                df_games["Image_Path"].isna()
                | (df_games["Image_Path"].astype(str).str.strip() == "")
                | df_games["Number of Players"].isna()
                | (df_games["Number of Players"].astype(str).str.strip() == "")
            )
            missing_games_df = df_games[missing_games_mask]

            if missing_games_df.empty:
                st.success("🎉 All titles in your Board Games database have complete details!")
            else:
                st.warning(f"Found {len(missing_games_df)} game(s) missing details or box art.")
                if st.button("🌐 Web Search BGG for Missing Game Data", key="btn_bgg_bulk_scan"):
                    game_scan_results = []
                    progress_bar = st.progress(0)

                    for i, (_, g_row) in enumerate(missing_games_df.iterrows()):
                        g_title = g_row["Title"]
                        matches = fetch_bgg_game_matches(g_title)
                        if matches:
                            details = fetch_bgg_game_details(matches[0]["id"])
                            game_scan_results.append({
                                "Original_Title": g_title,
                                "Title": matches[0]["name"],
                                "Found_Image": details.get("Image_Path", ""),
                                "Found_Players": details.get("Number of Players", ""),
                                "Found_Length": details.get("Length of Play", ""),
                                "Found_Age": details.get("Age Rating", ""),
                            })
                        progress_bar.progress((i + 1) / len(missing_games_df))

                    st.session_state["bulk_game_scan_results"] = game_scan_results
                    st.rerun()

                if st.session_state.get("bulk_game_scan_results") is not None:
                    st.markdown("#### Review Discovered Game Data")
                    g_results = st.session_state["bulk_game_scan_results"]

                    if not g_results:
                        st.info("No matching game art was found on BGG.")
                    else:
                        if st.button("⚡ Accept All BGG Web Updates", key="btn_accept_bgg_bulk"):
                            g_df_csv = safe_load_csv("board_and_card_games_collection.csv", ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
                            for item in g_results:
                                mask_g = g_df_csv["Title"].astype(str).str.lower().str.strip() == str(item["Original_Title"]).lower().strip()
                                if mask_g.any():
                                    idx = g_df_csv[mask_g].index[0]
                                    if item.get("Found_Image"):
                                        g_df_csv.at[idx, "Image_Path"] = item["Found_Image"]
                                    if item.get("Found_Players"):
                                        g_df_csv.at[idx, "Number of Players"] = item["Found_Players"]
                                    if item.get("Found_Length"):
                                        g_df_csv.at[idx, "Length of Play"] = item["Found_Length"]
                                    if item.get("Found_Age"):
                                        g_df_csv.at[idx, "Age Rating"] = item["Found_Age"]
                            g_df_csv.to_csv("board_and_card_games_collection.csv", index=False)
                            push_csv_to_github("board_and_card_games_collection.csv", "Bulk game metadata update")
                            st.session_state["bulk_game_scan_results"] = None
                            st.success("Updated game box art and details from BGG!")
                            st.rerun()

                        st.markdown("---")
                        g_cols = st.columns(3)
                        for idx_g_res, g_item in enumerate(g_results):
                            with g_cols[idx_g_res % 3]:
                                with st.container(border=True):
                                    safe_st_image(g_item["Found_Image"], use_container_width=True)
                                    st.markdown(f"**{g_item['Title']}**")
                                    st.caption(f"{g_item['Found_Players']} | {g_item['Found_Length']}")
                                    if st.button("✅ Accept Item", key=f"btn_accept_single_g_{idx_g_res}"):
                                        g_df_csv = safe_load_csv("board_and_card_games_collection.csv", ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
                                        mask_g = g_df_csv["Title"].astype(str).str.lower().str.strip() == str(g_item["Original_Title"]).lower().strip()
                                        if mask_g.any():
                                            idx = g_df_csv[mask_g].index[0]
                                            if g_item.get("Found_Image"):
                                                g_df_csv.at[idx, "Image_Path"] = g_item["Found_Image"]
                                            if g_item.get("Found_Players"):
                                                g_df_csv.at[idx, "Number of Players"] = g_item["Found_Players"]
                                            if g_item.get("Found_Length"):
                                                g_df_csv.at[idx, "Length of Play"] = g_item["Found_Length"]
                                            if g_item.get("Found_Age"):
                                                g_df_csv.at[idx, "Age Rating"] = g_item["Found_Age"]
                                            g_df_csv.to_csv("board_and_card_games_collection.csv", index=False)
                                            push_csv_to_github("board_and_card_games_collection.csv", f"Add BGG details for {g_item['Original_Title']}")
                                        st.session_state["bulk_game_scan_results"].pop(idx_g_res)
                                        st.rerun()

        st.markdown("---")
        display_finder_view(master_df[master_df["Category"] == "Board & Card Games"], "games")

    # KITCHEN GEAR TAB
    with tabs[3]:
        with st.expander("🛠️ Bulk Web Image Search & Review (Kitchen & Decor)"):
            st.write("Search close web product images across Kitchen and Decor items:")
            missing_k_mask = (
                df_kitchen["Image_Path"].isna()
                | (df_kitchen["Image_Path"].astype(str).str.strip() == "")
            )
            missing_k_df = df_kitchen[missing_k_mask]

            if missing_k_df.empty:
                st.success("🎉 All items in Kitchen Gear have photos!")
            else:
                st.warning(f"Found {len(missing_k_df)} kitchen/decor item(s) missing photos.")
                if st.button("🌐 Web Search for Missing Kitchen Images", key="btn_kitchen_bulk_gen"):
                    kitchen_scan_results = []
                    progress_bar = st.progress(0)
                    for idx_k, (_, k_row) in enumerate(missing_k_df.iterrows()):
                        k_name = k_row["Name of Item"]
                        found_urls = search_multiple_web_images(k_name, num_results=3)
                        if found_urls:
                            kitchen_scan_results.append({
                                "Name of Item": k_name,
                                "Candidate_Images": found_urls
                            })
                        progress_bar.progress((idx_k + 1) / len(missing_k_df))

                    st.session_state["bulk_kitchen_scan_results"] = kitchen_scan_results
                    st.rerun()

                if st.session_state.get("bulk_kitchen_scan_results") is not None:
                    st.markdown("#### Review Discovered Web Images")
                    k_results = st.session_state["bulk_kitchen_scan_results"]

                    if not k_results:
                        st.info("No web product photos were found for the missing items.")
                    else:
                        st.markdown("---")
                        for idx_res, res_item in enumerate(k_results):
                            st.markdown(f"**Item:** {res_item['Name of Item']}")
                            cand_cols = st.columns(3)
                            for cand_idx, img_url in enumerate(res_item["Candidate_Images"]):
                                with cand_cols[cand_idx % 3]:
                                    with st.container(border=True):
                                        safe_st_image(img_url, use_container_width=True)
                                        if st.button("✅ Accept Image", key=f"btn_accept_k_{idx_res}_{cand_idx}"):
                                            k_df_csv = safe_load_csv("kitchen_gear_inventory_v2.csv", ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"])
                                            mask_k = k_df_csv["Name of Item"].astype(str).str.lower().str.strip() == str(res_item["Name of Item"]).lower().strip()
                                            if mask_k.any():
                                                k_df_csv.loc[mask_k, "Image_Path"] = str(img_url)
                                                k_df_csv.to_csv("kitchen_gear_inventory_v2.csv", index=False)
                                                push_csv_to_github("kitchen_gear_inventory_v2.csv", f"Add image for {res_item['Name of Item']}")
                                            st.session_state["bulk_kitchen_scan_results"].pop(idx_res)
                                            st.rerun()

        st.markdown("---")
        display_finder_view(master_df[master_df["Category"] == "Kitchen Gear"], "kitchen")

    # DYNAMIC CUSTOM CATEGORIES TABS
    for i, custom_cat in enumerate(custom_cats):
        with tabs[4 + i]:
            key_cust = f"bulk_cust_scan_results_{i}"
            with st.expander(f"🛠️ Bulk Web Image Search & Review for {custom_cat['Name']}"):
                st.write(f"Search close web product images in bulk to auto-populate photos in **{custom_cat['Name']}**:")
                
                c_df_loaded = safe_load_csv(custom_cat["File"], custom_cat["Fields"])
                missing_cust_mask = (
                    c_df_loaded["Image_Path"].isna()
                    | (c_df_loaded["Image_Path"].astype(str).str.strip() == "")
                )
                missing_cust_df = c_df_loaded[missing_cust_mask]

                if missing_cust_df.empty:
                    st.success(f"🎉 All items in {custom_cat['Name']} have images!")
                else:
                    st.warning(f"Found {len(missing_cust_df)} item(s) missing images in {custom_cat['Name']}.")
                    if st.button(f"🌐 Search Web Images for {custom_cat['Name']}", key=f"btn_bulk_cust_web_gen_{i}"):
                        cust_scan_results = []
                        progress_bar = st.progress(0)
                        for idx_c, (_, c_row) in enumerate(missing_cust_df.iterrows()):
                            item_name_val = c_row[custom_cat["Primary_Col"]]
                            found_urls = search_multiple_web_images(item_name_val, num_results=3)
                            if found_urls:
                                cust_scan_results.append({
                                    "Item_Name": item_name_val,
                                    "Candidate_Images": found_urls
                                })
                            progress_bar.progress((idx_c + 1) / len(missing_cust_df))

                        st.session_state[key_cust] = cust_scan_results
                        st.rerun()

                    if st.session_state.get(key_cust) is not None:
                        st.markdown("#### Review Discovered Web Images")
                        cust_results = st.session_state[key_cust]

                        if not cust_results:
                            st.info("No web product photos were found for the missing items.")
                        else:
                            st.markdown("---")
                            for idx_c_res, c_item in enumerate(cust_results):
                                st.markdown(f"**Item:** {c_item['Item_Name']}")
                                c_cand_cols = st.columns(3)
                                for c_cand_idx, img_url in enumerate(c_item["Candidate_Images"]):
                                    with c_cand_cols[c_cand_idx % 3]:
                                        with st.container(border=True):
                                            safe_st_image(img_url, use_container_width=True)
                                            if st.button("✅ Accept Image", key=f"btn_accept_c_{i}_{idx_c_res}_{c_cand_idx}"):
                                                c_df_csv = safe_load_csv(custom_cat["File"], custom_cat["Fields"])
                                                mask_c = c_df_csv[custom_cat["Primary_Col"]].astype(str).str.lower().str.strip() == str(c_item["Item_Name"]).lower().strip()
                                                if mask_c.any():
                                                    c_df_csv.loc[mask_c, "Image_Path"] = str(img_url)
                                                    c_df_csv.to_csv(custom_cat["File"], index=False)
                                                    push_csv_to_github(custom_cat["File"], f"Add image for {c_item['Item_Name']}")
                                                st.session_state[key_cust].pop(idx_c_res)
                                                st.rerun()

            st.markdown("---")
            display_finder_view(master_df[master_df["Category"] == custom_cat["Name"]], f"custom_{i}")

    # ADD CATEGORY BUILDER TAB
    with tabs[-1]:
        st.subheader("🛠️ Create New Inventory Category")
        st.markdown("Configure a new inventory category schema. The app will automatically create a dedicated CSV file, sync it with GitHub, and generate custom forms for it.")

        st.markdown("##### 1. Live Web Emoji Search")
        selected_emoji = st.session_state.get("selected_category_emoji", "📦")
        emoji_search_q = st.text_input("🌐 Search Web Emoji Database", placeholder="Type keywords like 'pizza', 'camera', 'tool', 'car'...", key="web_emoji_search_q")
        web_emojis = search_emojis_online(emoji_search_q)

        with st.expander(f"🎨 Web Search Results ({len(web_emojis)} found | Current Selected: {selected_emoji})", expanded=True):
            grid_cols = st.columns(10)
            for idx, em in enumerate(web_emojis):
                with grid_cols[idx % 10]:
                    btn_type = "primary" if em == selected_emoji else "secondary"
                    if st.button(em, key=f"web_em_btn_{idx}_{em}", type=btn_type):
                        st.session_state["selected_category_emoji"] = em
                        st.rerun()

        st.markdown("---")
        st.markdown("##### 2. Category Details & Fields")

        with st.form("create_new_cat_form", clear_on_submit=True):
            col_c1, col_c2 = st.columns([3, 1])
            with col_c1:
                new_cat_name = st.text_input("New Category Name *", placeholder="e.g., Power Tools, Video Games, Books")
            with col_c2:
                st.text_input("Selected Icon", value=st.session_state.get("selected_category_emoji", "📦"), disabled=True)

            primary_col_name = st.text_input("Primary Item Name Field *", value="Item Name", help="The main name used to identify items in the file list.")
            
            raw_fields_input = st.text_area(
                "Fields/Columns to Track (comma-separated) *",
                value="Brand, Model, Serial Number, Purchase Date, Price",
                help="Enter all the attributes you want to track for this category, separated by commas."
            )

            if st.form_submit_button("🚀 Save Category & Initialize Database"):
                if new_cat_name and primary_col_name and raw_fields_input:
                    parsed_fields = [f.strip() for f in raw_fields_input.split(",") if f.strip()]
                    chosen_icon = st.session_state.get("selected_category_emoji", "📦")
                    save_custom_category(new_cat_name, chosen_icon, primary_col_name, parsed_fields)
                    st.success(f"🎉 Created '{new_cat_name}' category with icon {chosen_icon}! Synced to GitHub.")
                    st.rerun()
                else:
                    st.error("Please fill in the category name, primary item field, and at least one attribute field.")
