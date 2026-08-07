import os
import urllib.parse
import xml.etree.ElementTree as ET
import difflib
import pandas as pd
import requests
import streamlit as st
from github import Github

# -----------------------------------------------------------------------------
# 1. PAGE CONFIG & MACOS FINDER STYLING
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

OMDB_API_KEY = os.getenv("OMDB_KEY", "")
BGG_API_TOKEN = os.getenv("BGG_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")

# Default Fallback Emojis
DEFAULT_EMOJI_GRID = ["📦", "📁", "🧰", "🎬", "🎲", "🍳", "🛠️", "💻", "🎮", "📚", "👕", "🛋️", "📷", "🔒", "🏠"]


# -----------------------------------------------------------------------------
# 2. WEB EMOJI SEARCH HELPER (LIVE WEB QUERY)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def search_emojis_online(search_query):
    """Fetches matching unicode emojis via open web API."""
    if not search_query or not search_query.strip():
        return DEFAULT_EMOJI_GRID

    try:
        q = urllib.parse.quote_plus(search_query.strip().lower())
        url = f"https://emoji-api.com/emojis?search={q}&access_key=77626372b3a0e2a22906b3a0428648e1d2e1320d"
        res = requests.get(url, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                return [item["character"] for item in data[:30] if "character" in item]
    except Exception:
        pass

    # Local fallback search if web query encounters rate limits or offline state
    fallback_matches = []
    for em in DEFAULT_EMOJI_GRID:
        if search_query.lower() in em:
            fallback_matches.append(em)
    return fallback_matches or DEFAULT_EMOJI_GRID


# -----------------------------------------------------------------------------
# 3. GITHUB SYNC HELPER FUNCTION
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


# -----------------------------------------------------------------------------
# 4. DATA & SCHEMA HELPERS
# -----------------------------------------------------------------------------
CUSTOM_CATEGORIES_FILE = "custom_categories_registry.csv"

def safe_load_csv(file_path, expected_columns):
    if not os.path.exists(file_path):
        return pd.DataFrame(columns=expected_columns)
    try:
        df = pd.read_csv(file_path, on_bad_lines="skip")
        for col in expected_columns:
            if col not in df.columns:
                df[col] = ""
        return df
    except Exception as e:
        st.error(f"Error reading {file_path}: {e}")
        return pd.DataFrame(columns=expected_columns)


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


def fetch_bgg_game_details(bgg_id):
    if not bgg_id:
        return {}

    try:
        url = f"https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}"
        headers = {
            "User-Agent": "HomeInventoryApp/1.0 (Python Streamlit Inventory Tool)",
            "Accept": "text/xml,application/xml"
        }
        if BGG_API_TOKEN:
            headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"

        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            item = root.find("item")
            if item is not None:
                image_elem = item.find("thumbnail")
                if image_elem is None or not image_elem.text:
                    image_elem = item.find("image")
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
    return {}


def fetch_collection_movies(collection_title):
    if not OMDB_API_KEY:
        return []
    
    clean_q = collection_title.lower()
    keywords = ["collection", "trilogy", "quadrilogy", "anthology", "series", "box set", "film set", "bundle", "franchise", "1-", "2-", "3-", "4-", "5-", "6-", "7-", "8-", "9-", "movie", "films"]
    search_term = clean_q
    for kw in keywords:
        search_term = search_term.replace(kw, "")
    search_term = search_term.strip() or collection_title

    try:
        encoded_q = urllib.parse.quote_plus(search_term)
        url = f"http://www.omdbapi.com/?s={encoded_q}&type=movie&apikey={OMDB_API_KEY}"
        res = requests.get(url, timeout=5).json()

        if res.get("Response") == "True":
            results = res.get("Search", [])
            detailed_items = []
            for item in results[:8]:
                d_url = f"http://www.omdbapi.com/?i={item['imdbID']}&apikey={OMDB_API_KEY}"
                d_res = requests.get(d_url, timeout=4).json()
                if d_res.get("Response") == "True":
                    detailed_items.append({
                        "Title": d_res.get("Title", ""),
                        "Year Released": d_res.get("Year", ""),
                        "Rating": d_res.get("Rated", ""),
                        "Length of Movie": d_res.get("Runtime", ""),
                        "Type": d_res.get("Type", "movie").capitalize(),
                        "Genre": d_res.get("Genre", ""),
                        "Image_Path": d_res.get("Poster", "") if d_res.get("Poster") != "N/A" else ""
                    })
            return detailed_items
    except Exception as e:
        st.error(f"Error expanding collection: {e}")
    return []


def save_multiple_movies_to_csv(file_path, movies_list):
    expected_cols = ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"]
    existing_df = safe_load_csv(file_path, expected_cols)
    new_df = pd.DataFrame(movies_list)
    
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
    df = pd.read_csv(file_path, on_bad_lines="skip")
    df = df.astype(object)

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
# 5. AUTHENTICATION
# -----------------------------------------------------------------------------
PIN_CODE = "1234"


def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("🏠 Home Inventory Access")
        input_pin = st.text_input("Enter Invite Code / PIN:", type="password", key="pin_input")
        if st.button("Login"):
            if input_pin == PIN_CODE:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Invalid invite code.")
        return False
    return True


if check_password():
    custom_cats = load_custom_categories()

    # -----------------------------------------------------------------------------
    # 6. FINDER TOP TOOLBAR
    # -----------------------------------------------------------------------------
    if "finder_sort_col" not in st.session_state:
        st.session_state["finder_sort_col"] = "Name"
    if "finder_sort_asc" not in st.session_state:
        st.session_state["finder_sort_asc"] = True
    if "finder_view_mode" not in st.session_state:
        st.session_state["finder_view_mode"] = "List"
    if "selected_category_emoji" not in st.session_state:
        st.session_state["selected_category_emoji"] = "📦"

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
            st.session_state["authenticated"] = False
            st.rerun()

    st.markdown("---")

    # -----------------------------------------------------------------------------
    # 7. ADD ITEM DRAWER
    # -----------------------------------------------------------------------------
    if st.session_state.get("show_add_form", False):
        with st.container(border=True):
            st.subheader("➕ Add New Inventory Item")
            
            category_options = ["Movies & TV", "Board & Card Games", "Kitchen Gear"] + [c["Name"] for c in custom_cats]
            category = st.selectbox("Select Item Category", category_options)

            if category == "Movies & TV":
                search_title = st.text_input("Search Title or Collection", placeholder="e.g., The Dark Knight Trilogy")
                if st.button("🔍 Search Movie Database") and search_title:
                    unpacked = fetch_collection_movies(search_title)
                    if unpacked:
                        st.session_state["unpacked_collection"] = unpacked
                    else:
                        encoded_q = urllib.parse.quote_plus(search_title.strip())
                        res = requests.get(f"http://www.omdbapi.com/?s={encoded_q}&apikey={OMDB_API_KEY}", timeout=5).json()
                        st.session_state["search_results"] = res.get("Search", [])

                if st.session_state.get("unpacked_collection"):
                    if st.button("🚀 Add All Movies from Collection"):
                        save_multiple_movies_to_csv("movies_and_tv_collection.csv", st.session_state["unpacked_collection"])
                        st.session_state["show_add_form"] = False
                        st.rerun()

                with st.form("movie_form", clear_on_submit=True):
                    title = st.text_input("Title *")
                    rating = st.text_input("Rating")
                    year = st.text_input("Year Released")
                    length = st.text_input("Length of Movie")
                    m_type = st.selectbox("Type", ["Movie", "TV", "Collection"])
                    genre = st.text_input("Genre")
                    poster_link = st.text_input("Poster URL")
                    if st.form_submit_button("Save Movie"):
                        if title:
                            new_entry = {"Title": title, "Rating": rating, "Year Released": year, "Length of Movie": length, "Type": m_type, "Genre": genre, "Image_Path": poster_link}
                            df = safe_load_csv("movies_and_tv_collection.csv", list(new_entry.keys()))
                            pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv("movies_and_tv_collection.csv", index=False)
                            push_csv_to_github("movies_and_tv_collection.csv", f"Add movie '{title}'")
                            st.session_state["show_add_form"] = False
                            st.rerun()

            elif category == "Board & Card Games":
                g_search = st.text_input("Search Game Title")
                if st.button("🔍 Search BGG") and g_search:
                    st.session_state["bgg_results"] = fetch_bgg_game_matches(g_search)

                with st.form("game_form", clear_on_submit=True):
                    title = st.text_input("Game Title *")
                    players = st.text_input("Number of Players")
                    length = st.text_input("Length of Play")
                    age = st.text_input("Age Rating")
                    box_url = st.text_input("Box Photo URL")
                    if st.form_submit_button("Save Game"):
                        if title:
                            new_entry = {"Title": title, "Number of Players": players, "Length of Play": length, "Age Rating": age, "Style of Game": "Board", "Image_Path": box_url}
                            df = safe_load_csv("board_and_card_games_collection.csv", list(new_entry.keys()))
                            pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv("board_and_card_games_collection.csv", index=False)
                            push_csv_to_github("board_and_card_games_collection.csv", f"Add game '{title}'")
                            st.session_state["show_add_form"] = False
                            st.rerun()

            elif category == "Kitchen Gear":
                with st.form("kitchen_form", clear_on_submit=True):
                    title = st.text_input("Name of Item *")
                    eq_type = st.selectbox("Type of Equipment", ["Appliance", "Cookware", "Utensil"])
                    manual = st.text_input("Manual Link URL")
                    image_url = st.text_input("Photo Image URL")
                    uploaded_image = st.file_uploader("Or Upload Custom Item Photo File", type=["jpg", "png", "jpeg"])

                    if st.form_submit_button("Save Kitchen Item"):
                        if title:
                            final_img = image_url
                            if uploaded_image:
                                local_path = os.path.join(IMAGE_DIR, uploaded_image.name)
                                with open(local_path, "wb") as f:
                                    f.write(uploaded_image.getbuffer())
                                final_img = local_path

                            new_entry = {"Name of Item": title, "Type of Equipment": eq_type, "Instruction Manual Link": manual, "Image_Path": final_img}
                            df = safe_load_csv("kitchen_gear_inventory_v2.csv", list(new_entry.keys()))
                            pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True).to_csv("kitchen_gear_inventory_v2.csv", index=False)
                            push_csv_to_github("kitchen_gear_inventory_v2.csv", f"Add kitchen gear '{title}'")
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
                            final_img = cust_img_url
                            if cust_file:
                                local_path = os.path.join(IMAGE_DIR, cust_file.name)
                                with open(local_path, "wb") as f:
                                    f.write(cust_file.getbuffer())
                                final_img = local_path
                            
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
    # 9. GLOBAL FUZZY SEARCH BAR & DYNAMIC FINDER NAVIGATION TABS
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

        uploaded_img_file = st.file_uploader(f"Upload Image for {item_id}", type=["jpg", "png", "jpeg"], key=f"upload_{unique_key_id}")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Changes", key=f"save_{unique_key_id}"):
                if uploaded_img_file:
                    local_img_path = os.path.join(IMAGE_DIR, uploaded_img_file.name)
                    with open(local_img_path, "wb") as f:
                        f.write(uploaded_img_file.getbuffer())
                    edit_inputs["Image_Path"] = local_img_path

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
                    if pd.notna(img_val) and str(img_val).strip() != "":
                        st.image(str(img_val), width=24)
                    else:
                        st.write("📄")

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
                        if pd.notna(img_val) and str(img_val).strip() != "":
                            st.image(str(img_val), use_container_width=True)
                        else:
                            st.caption("📁 Folder Item")

                        st.markdown(f"**{item_name}**")
                        st.caption(cat)

                        with st.expander("✏️ Open / Edit"):
                            render_edit_drawer(unique_key_id, item_name, row, editable_cols, file_path, title_col)

    # -----------------------------------------------------------------------------
    # 10. TAB RENDERING WITH DYNAMIC ONLINE EMOJI WEB SEARCH
    # -----------------------------------------------------------------------------
    with tabs[0]:
        display_finder_view(master_df, "master")

    with tabs[1]:
        display_finder_view(master_df[master_df["Category"] == "Movies & TV"], "movies")

    with tabs[2]:
        display_finder_view(master_df[master_df["Category"] == "Board & Card Games"], "games")

    with tabs[3]:
        display_finder_view(master_df[master_df["Category"] == "Kitchen Gear"], "kitchen")

    for i, custom_cat in enumerate(custom_cats):
        with tabs[4 + i]:
            display_finder_view(master_df[master_df["Category"] == custom_cat["Name"]], f"custom_{i}")

    # "➕ Add Category" Builder Tab with Web-Based Emoji Search
    with tabs[-1]:
        st.subheader("🛠️ Create New Inventory Category")
        st.markdown("Configure a new inventory category schema. The app will automatically create a dedicated CSV file, sync it with GitHub, and generate custom forms for it.")

        st.markdown("##### 1. Live Web Emoji Search")
        
        selected_emoji = st.session_state.get("selected_category_emoji", "📦")
        
        emoji_search_q = st.text_input("🌐 Search Web Emoji Database", placeholder="Type keywords like 'pizza', 'camera', 'tool', 'car'...", key="web_emoji_search_q")
        
        # Query online web API for emojis matching search query
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
