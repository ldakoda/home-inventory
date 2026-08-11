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
# 1. PAGE CONFIG, CONSTANTS & STYLING
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

# File Path Constants
MOVIES_CSV = "movies_and_tv_collection.csv"
GAMES_CSV = "board_and_card_games_collection.csv"
KITCHEN_CSV = "kitchen_gear_inventory_v2.csv"
CUSTOM_CATEGORIES_FILE = "custom_categories_registry.csv"

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
    if not query_text or not str(query_text).strip():
        return []
    raw_query = str(query_text).strip()
    clean_q = re.sub(r"[^\w\s]", "", raw_query)
    results = []

    try:
        with DDGS() as ddgs:
            res = list(ddgs.images(clean_q, max_results=num_results))
            for r in res:
                img_url = r.get("image") or r.get("thumbnail")
                if img_url and img_url not in results:
                    results.append(img_url)
    except Exception:
        pass

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

    if len(results) < 3:
        for search_term in [clean_q, " ".join(clean_q.split()[:2])]:
            try:
                encoded_q = urllib.parse.quote_plus(search_term)
                wiki_url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded_q}&gsrlimit=10&prop=pageimages&pithumbsize=500&format=json"
                res = requests.get(wiki_url, headers={"User-Agent": "HomeInventoryApp/1.0"}, timeout=5)
                if res.status_code == 200:
                    pages = res.json().get("query", {}).get("pages", {})
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
                path=file_path, message=commit_message,
                content=content, sha=repo_file.sha, branch="main"
            )
        except Exception:
            repo.create_file(
                path=file_path, message=commit_message,
                content=content, branch="main"
            )
        st.toast(f"✅ Synced `{file_path}` to GitHub!", icon="🚀")
        return True
    except Exception as e:
        st.error(f"GitHub Sync Error: {e}")
        return False

def push_image_to_github(uploaded_file):
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
                path=github_path, message=f"Update image {filename}",
                content=file_bytes, sha=existing_file.sha, branch="main"
            )
        except Exception:
            repo.create_file(
                path=github_path, message=f"Upload image {filename}",
                content=file_bytes, branch="main"
            )
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{github_path}"
    except Exception as e:
        st.error(f"Failed to upload image to GitHub: {e}")
        local_path = os.path.join(IMAGE_DIR, filename)
        with open(local_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        return local_path

# -----------------------------------------------------------------------------
# 4. DATA & SCHEMA HELPERS
# -----------------------------------------------------------------------------
@st.cache_data
def safe_load_csv(file_path, expected_columns):
    if not os.path.exists(file_path):
        return pd.DataFrame(columns=expected_columns).astype(object)
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
        categories = df.to_dict('records')
        formatted_cats = []
        for row in categories:
            fields = [f.strip() for f in str(row.get("Fields", "")).split(",") if f.strip()]
            formatted_cats.append({
                "Name": str(row.get("Category Name", "")),
                "Icon": str(row.get("Icon", "")),
                "File": str(row.get("File Path", "")),
                "Primary_Col": str(row.get("Primary Col", "")),
                "Fields": fields,
            })
        return formatted_cats
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
        "Category Name": cat_name, "Icon": icon,
        "File Path": clean_filename, "Primary Col": primary_col,
        "Fields": ",".join(fields_list)
    }
    updated_reg = pd.concat([reg_df, pd.DataFrame([new_reg])], ignore_index=True)
    updated_reg.to_csv(CUSTOM_CATEGORIES_FILE, index=False)
    push_csv_to_github(CUSTOM_CATEGORIES_FILE, f"Register category '{cat_name}'")
    st.cache_data.clear()

def fetch_bgg_game_matches(game_title):
    if not game_title or not game_title.strip(): return []
    try:
        encoded_q = urllib.parse.quote_plus(game_title.strip())
        url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame"
        headers = {"User-Agent": "HomeInventoryApp/1.0", "Accept": "text/xml"}
        if BGG_API_TOKEN: headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"
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

def fetch_bgg_game_details(bgg_id):
    if not bgg_id: return {}
    try:
        url = f"https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}"
        headers = {"User-Agent": "HomeInventoryApp/1.0", "Accept": "text/xml"}
        if BGG_API_TOKEN: headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            item = root.find("item")
            if item is not None:
                image_elem = item.find("thumbnail") or item.find("image")
                image_url = image_elem.text if image_elem is not None else ""
                min_p = item.find("minplayers").attrib.get("value") if item.find("minplayers") is not None else ""
                max_p = item.find("maxplayers").attrib.get("value") if item.find("maxplayers") is not None else ""
                players = f"{min_p}-{max_p} Players" if min_p and max_p and min_p != max_p else f"{min_p} Players"
                min_t = item.find("minplaytime").attrib.get("value") if item.find("minplaytime") is not None else ""
                max_t = item.find("maxplaytime").attrib.get("value") if item.find("maxplaytime") is not None else ""
                length = f"{min_t}-{max_t} min" if min_t and max_t and min_t != max_t else f"{min_t} min"
                age = item.find("minage").attrib.get("value") if item.find("minage") is not None else ""
                if age and age != "0": age = f"{age}+"
                return {
                    "Image_Path": image_url, "Number of Players": players,
                    "Length of Play": length, "Age Rating": age,
                }
    except Exception as e:
        st.error(f"BGG Detail Fetch Error: {e}")
    return {}

def fetch_collection_movies(collection_title):
    if not OMDB_API_KEY: return []
    clean_q = collection_title.lower()
    keywords = ["collection", "trilogy", "quadrilogy", "anthology", "series", "box set", "film set", "bundle", "franchise", "1-", "2-", "3-", "4-", "5-", "6-", "7-", "8-", "9-", "movie", "films"]
    search_term = clean_q
    for kw in keywords: search_term = search_term.replace(kw, "")
    search_term = search_term.strip() or collection_title
    
    try:
        encoded_q = urllib.parse.quote_plus(search_term)
        url = f"http://www.omdbapi.com/?s={encoded_q}&type=movie&apikey={OMDB_API_KEY}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200 and res.json().get("Response") == "True":
            results = res.json().get("Search", [])
            detailed_items = []
            for item in results[:8]:
                d_url = f"http://www.omdbapi.com/?i={item['imdbID']}&apikey={OMDB_API_KEY}"
                d_res = requests.get(d_url, timeout=4)
                if d_res.status_code == 200 and d_res.json().get("Response") == "True":
                    d_data = d_res.json()
                    detailed_items.append({
                        "Title": d_data.get("Title", ""), "Year Released": d_data.get("Year", ""),
                        "Rating": d_data.get("Rated", ""), "Length of Movie": d_data.get("Runtime", ""),
                        "Type": d_data.get("Type", "movie").capitalize(), "Genre": d_data.get("Genre", ""),
                        "Image_Path": d_data.get("Poster", "") if d_data.get("Poster") != "N/A" else ""
                    })
            return detailed_items
    except Exception as e:
        st.error(f"Error expanding collection: {e}")
    return []

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
    st.cache_data.clear()
    return True

def save_edited_row(file_path, original_title_or_name, updated_row_dict, key_col):
    df = pd.read_csv(file_path, on_bad_lines="skip").astype(object)
    mask = (df[key_col].astype(str).str.lower().str.strip() == str(original_title_or_name).lower().strip())
    
    if not mask.any(): return False
    idx = df[mask].index[0]
    
    if updated_row_dict.get("_DELETE_"):
        df = df.drop(idx).reset_index(drop=True)
        msg = f"Delete item '{original_title_or_name}'"
    else:
        for k, v in updated_row_dict.items():
            if k in df.columns: df.at[idx, k] = str(v)
        msg = f"Edit item '{original_title_or_name}'"
        
    df.to_csv(file_path, index=False)
    push_csv_to_github(file_path, msg)
    st.cache_data.clear()
    return True

# -----------------------------------------------------------------------------
# 5. AUTHENTICATION & CORE LOGIC
# -----------------------------------------------------------------------------
PIN_CODE = "1234" # Left hardcoded per user request

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
                cookie_manager.set("home_inventory_auth_token", "logged_in_30_days_valid", key="set_auth_cookie", max_age=30 * 86400)
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

def load_and_merge_data(custom_cats):
    """Loads and standardizes all tables into a master view DataFrame."""
    df_m = safe_load_csv(MOVIES_CSV, ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
    df_g = safe_load_csv(GAMES_CSV, ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
    df_k = safe_load_csv(KITCHEN_CSV, ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"])

    m_df = df_m.copy()
    m_df["Name"] = m_df["Title"]
    m_df["Category"] = "Movies & TV"
    m_df["Kind"] = m_df["Type"].fillna("Movie")
    m_df["Details"] = "Year: " + m_df["Year Released"].astype(str) + " | " + m_df["Genre"].astype(str)
    m_df["_File"] = MOVIES_CSV
    m_df["_TitleCol"] = "Title"
    m_df["_Cols"] = [["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"]] * len(m_df)

    g_df = df_g.copy()
    g_df["Name"] = g_df["Title"]
    g_df["Category"] = "Board & Card Games"
    g_df["Kind"] = g_df["Style of Game"].fillna("Game")
    g_df["Details"] = "Players: " + g_df["Number of Players"].astype(str) + " | " + g_df["Length of Play"].astype(str)
    g_df["_File"] = GAMES_CSV
    g_df["_TitleCol"] = "Title"
    g_df["_Cols"] = [["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"]] * len(g_df)

    k_df = df_k.copy()
    k_df["Name"] = k_df["Name of Item"]
    k_df["Category"] = "Kitchen Gear"
    k_df["Kind"] = k_df["Type of Equipment"].fillna("Kitchen")
    k_df["Details"] = "Equipment: " + k_df["Type of Equipment"].astype(str)
    k_df["_File"] = KITCHEN_CSV
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

    master = pd.concat([m_df, g_df, k_df] + custom_dfs, ignore_index=True)
    return master, df_g, df_k

# -----------------------------------------------------------------------------
# MAIN APP EXECUTION
# -----------------------------------------------------------------------------
if check_password():
    custom_cats = load_custom_categories()

    # Session State Initialization
    if "bulk_kitchen_scan_results" not in st.session_state: st.session_state["bulk_kitchen_scan_results"] = None
    if "bulk_game_scan_results" not in st.session_state: st.session_state["bulk_game_scan_results"] = None
    for i, c_cat in enumerate(custom_cats):
        if f"bulk_cust_scan_results_{i}" not in st.session_state:
            st.session_state[f"bulk_cust_scan_results_{i}"] = None

    if "finder_sort_col" not in st.session_state: st.session_state["finder_sort_col"] = "Name"
    if "finder_sort_asc" not in st.session_state: st.session_state["finder_sort_asc"] = True
    if "finder_view_mode" not in st.session_state: st.session_state["finder_view_mode"] = "List"

    # Toolbar
    toolbar_col1, toolbar_col2, toolbar_col3 = st.columns([5, 3.5, 1.5])
    with toolbar_col1: st.markdown("### 📁 Desktop / Home Inventory")
    with toolbar_col2:
        v1, v2, v3, v4 = st.columns(4)
        if v1.button("⊞ Icons", use_container_width=True): st.session_state["finder_view_mode"] = "Icons"; st.rerun()
        if v2.button("☰ List", use_container_width=True): st.session_state["finder_view_mode"] = "List"; st.rerun()
        if v3.button("|| Columns", use_container_width=True): st.session_state["finder_view_mode"] = "Columns"; st.rerun()
        if v4.button("➕ Add", use_container_width=True): 
            st.session_state["show_add_form"] = not st.session_state.get("show_add_form", False)
            st.rerun()
    with toolbar_col3:
        if st.button("🚪 Log Out", use_container_width=True): logout_user()

    st.markdown("---")

    # Add Item Drawer
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
                    uploaded_image = st.file_uploader("Or Upload Custom Poster File", type=["jpg", "png", "jpeg"])
                    if st.form_submit_button("Save Movie Entry") and title:
                        final_img = push_image_to_github(uploaded_image) if uploaded_image else poster_link
                        new_entry = {"Title": title, "Rating": rating, "Year Released": year, "Length of Movie": length, "Type": m_type, "Genre": genre, "Image_Path": final_img}
                        df = safe_load_csv(MOVIES_CSV, list(new_entry.keys()))
                        pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv(MOVIES_CSV, index=False)
                        push_csv_to_github(MOVIES_CSV, f"Add movie '{title}'")
                        st.cache_data.clear()
                        st.session_state["show_add_form"] = False
                        st.rerun()
                        
            elif category == "Board & Card Games":
                with st.form("game_form", clear_on_submit=True):
                    title = st.text_input("Game Title *")
                    players = st.text_input("Number of Players")
                    length = st.text_input("Length of Play")
                    age = st.text_input("Age Rating")
                    box_url = st.text_input("Box Photo URL")
                    uploaded_image = st.file_uploader("Or Upload Custom Box Art File", type=["jpg", "png", "jpeg"])
                    if st.form_submit_button("Save Game") and title:
                        final_img = push_image_to_github(uploaded_image) if uploaded_image else box_url
                        new_entry = {"Title": title, "Number of Players": players, "Length of Play": length, "Age Rating": age, "Style of Game": "Board", "Image_Path": final_img}
                        df = safe_load_csv(GAMES_CSV, list(new_entry.keys()))
                        pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv(GAMES_CSV, index=False)
                        push_csv_to_github(GAMES_CSV, f"Add game '{title}'")
                        st.cache_data.clear()
                        st.session_state["show_add_form"] = False
                        st.rerun()
                        
            elif category == "Kitchen Gear":
                with st.form("kitchen_form", clear_on_submit=True):
                    title = st.text_input("Name of Item *")
                    eq_type = st.selectbox("Type of Equipment", ["Appliance", "Cookware", "Utensil", "Decoration"])
                    manual = st.text_input("Manual Link URL")
                    image_url = st.text_input("Photo Image URL")
                    uploaded_image = st.file_uploader("Or Upload Custom Photo File", type=["jpg", "png", "jpeg"])
                    if st.form_submit_button("Save Kitchen / Decor Item") and title:
                        final_img = push_image_to_github(uploaded_image) if uploaded_image else image_url
                        new_entry = {"Name of Item": title, "Type of Equipment": eq_type, "Instruction Manual Link": manual, "Image_Path": final_img}
                        df = safe_load_csv(KITCHEN_CSV, list(new_entry.keys()))
                        pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv(KITCHEN_CSV, index=False)
                        push_csv_to_github(KITCHEN_CSV, f"Add kitchen item '{title}'")
                        st.cache_data.clear()
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
                    cust_file = st.file_uploader("Or Upload Image File", type=["jpg", "png", "jpeg"])
                    if st.form_submit_button(f"Save to {target_cat['Name']}"):
                        primary_val = custom_data.get(target_cat["Primary_Col"])
                        if primary_val:
                            final_img = push_image_to_github(cust_file) if cust_file else cust_img_url
                            custom_data["Image_Path"] = final_img
                            df = safe_load_csv(target_cat["File"], target_cat["Fields"])
                            pd.concat([df, pd.DataFrame([custom_data])], ignore_index=True).to_csv(target_cat["File"], index=False)
                            push_csv_to_github(target_cat["File"], f"Add {category} item '{primary_val}'")
                            st.cache_data.clear()
                            st.session_state["show_add_form"] = False
                            st.rerun()
        st.markdown("---")

    # Master Data Prep
    master_df, df_games, df_kitchen = load_and_merge_data(custom_cats)

    finder_search_q = st.text_input("🔍 Search Desktop Files (fuzzy & typo matching)...", key="finder_search_q")
    if finder_search_q:
        query = finder_search_q.strip().lower()
        def is_fuzzy_match(name):
            name_str = str(name).lower()
            if query in name_str or any(q_word in name_str for q_word in query.split()): return True
            return difflib.SequenceMatcher(None, query, name_str).ratio() >= 0.50
        master_df = master_df[master_df["Name"].apply(is_fuzzy_match)]

    tab_names = ["🌐 All Files (Master)", "🎬 Movies & TV", "🎲 Board & Card Games", "🍳 Kitchen Gear"] + [f"{c['Icon']} {c['Name']}" for c in custom_cats] + ["➕ Add Category"]
    tabs = st.tabs(tab_names)

    # Drawer / Views logic
    def render_edit_drawer(unique_key_id, item_id, row, editable_cols, file_path, title_col):
        edit_inputs = {}
        for col_name in editable_cols:
            input_key = f"edit_{unique_key_id}_{col_name}"
            if input_key not in st.session_state: st.session_state[input_key] = str(row.get(col_name, ""))
            edit_inputs[col_name] = st.text_input(f"{col_name}", key=input_key)
            
        uploaded_img_file = st.file_uploader(f"Upload Image File for {item_id}", type=["jpg", "png", "jpeg"], key=f"upload_{unique_key_id}")
        category_type = str(row.get("Category", ""))
        
        if category_type == "Kitchen Gear" or any(c["Name"] == category_type for c in custom_cats):
            st.markdown("---")
            search_query_input = st.text_input("Web Search Terms", value=item_id, key=f"web_q_{unique_key_id}")
            if st.button("🔍 Search Web Photos", key=f"btn_search_web_{unique_key_id}"):
                st.session_state[f"edit_search_results_{unique_key_id}"] = search_multiple_web_images(search_query_input, num_results=8)
            if st.session_state.get(f"edit_search_results_{unique_key_id}"):
                c_results = st.session_state[f"edit_search_results_{unique_key_id}"]
                if not c_results: st.info("No close web photos found.")
                else:
                    grid_cols = st.columns(4)
                    for idx_img, img_url in enumerate(c_results):
                        with grid_cols[idx_img % 4]:
                            safe_st_image(img_url, use_container_width=True)
                            if st.button("✅ Pick", key=f"btn_apply_img_{unique_key_id}_{idx_img}"):
                                edit_inputs["Image_Path"] = img_url
                                if save_edited_row(file_path, item_id, edit_inputs, title_col):
                                    st.session_state[f"expand_edit_{unique_key_id}"] = False
                                    st.session_state.pop(f"edit_search_results_{unique_key_id}", None)
                                    st.rerun()

        if category_type == "Movies & TV" and "collection" in str(row.get("Type", "")).lower():
            st.markdown("---")
            if st.button("🔍 Auto-Unpack Child Movies", key=f"unpack_nested_{unique_key_id}"):
                unpacked_childs = fetch_collection_movies(item_id)
                if unpacked_childs:
                    save_multiple_movies_to_csv(MOVIES_CSV, unpacked_childs)
                    st.rerun()
        st.markdown("---")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Changes", key=f"save_{unique_key_id}"):
                if uploaded_img_file: edit_inputs["Image_Path"] = push_image_to_github(uploaded_img_file)
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
            
        sort_col, sort_asc = st.session_state["finder_sort_col"], st.session_state["finder_sort_asc"]
        if sort_col in df_subset.columns:
            df_subset = df_subset.sort_values(by=sort_col, ascending=sort_asc, key=lambda x: x.astype(str).str.lower()).reset_index(drop=True)

        if st.session_state["finder_view_mode"] in ["List", "Columns"]:
            for idx, row in df_subset.iterrows():
                unique_key_id = f"{tab_key_prefix}_{idx}_{hash(str(row['Name']))}"
                c1, c2, c3, c4, c5 = st.columns([0.4, 3.5, 1.8, 1.8, 0.8], vertical_alignment="center")
                with c1: safe_st_image(row.get("Image_Path", ""), width=24)
                with c2: st.markdown(f"**{row['Name']}**")
                with c3: st.caption(row["Category"])
                with c4: st.caption(row["Kind"])
                with c5:
                    exp_key = f"expand_edit_{unique_key_id}"
                    if st.button("✏️ Edit", key=f"btn_edit_{unique_key_id}"):
                        st.session_state[exp_key] = not st.session_state.get(exp_key, False)
                if st.session_state.get(exp_key, False):
                    render_edit_drawer(unique_key_id, row["Name"], row, row["_Cols"], row["_File"], row["_TitleCol"])
        else:
            cols = st.columns(4)
            for idx, row in df_subset.iterrows():
                with cols[idx % 4]:
                    with st.container(border=True):
                        safe_st_image(row.get("Image_Path", ""), use_container_width=True)
                        st.markdown(f"**{row['Name']}**")
                        st.caption(row["Category"])
                        with st.expander("✏️ Open / Edit"):
                            render_edit_drawer(f"icon_{tab_key_prefix}_{idx}_{hash(str(row['Name']))}", row["Name"], row, row["_Cols"], row["_File"], row["_TitleCol"])

    # Tabs Generation
    with tabs[0]: display_finder_view(master_df, "master")
    
    with tabs[1]:
        with st.expander("🛠️ Bulk Movie Web Search"):
            if st.button("🔍 Search & Add", key="btn_bulk_m_exec"):
                unpacked_f = fetch_collection_movies(st.text_input("Title", key="bulk_m_query"))
                if unpacked_f:
                    save_multiple_movies_to_csv(MOVIES_CSV, unpacked_f)
                    st.rerun()
        display_finder_view(master_df[master_df["Category"] == "Movies & TV"], "movies")
        
    with tabs[2]:
        display_finder_view(master_df[master_df["Category"] == "Board & Card Games"], "games")
        
    with tabs[3]:
        display_finder_view(master_df[master_df["Category"] == "Kitchen Gear"], "kitchen")
        
    for i, c_cat in enumerate(custom_cats):
        with tabs[4 + i]:
            display_finder_view(master_df[master_df["Category"] == c_cat["Name"]], f"custom_{i}")
            
    with tabs[-1]:
        st.subheader("🛠️ Create New Inventory Category")
        with st.form("create_new_cat_form", clear_on_submit=True):
            new_cat_name = st.text_input("New Category Name *")
            primary_col_name = st.text_input("Primary Item Name Field *", value="Item Name")
            raw_fields_input = st.text_area("Fields (comma-separated) *", value="Brand, Model, Price")
            
            if st.form_submit_button("🚀 Save Category") and new_cat_name:
                parsed_fields = [f.strip() for f in raw_fields_input.split(",") if f.strip()]
                save_custom_category(new_cat_name, "📦", primary_col_name, parsed_fields)
                st.rerun()
