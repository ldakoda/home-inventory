import os
import urllib.parse
import xml.etree.ElementTree as ET
import pandas as pd
import requests
import streamlit as st
from github import Github

# -----------------------------------------------------------------------------
# 1. PAGE CONFIG & STYLING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Home Inventory System",
    page_icon="🏠",
    layout="wide",
)

# Custom CSS for compact list spacing and hiding sidebar
st.markdown(
    """
    <style>
    div[data-testid="stVerticalBlock"] > div {
        gap: 0.2rem !important;
    }
    /* Hide Streamlit default sidebar collapse chevron if present */
    [data-testid="stSidebarNav"] {display: none;}
    </style>
    """,
    unsafe_allow_html=True,
)

# Directory for storing user-uploaded images locally
IMAGE_DIR = "uploaded_images"
os.makedirs(IMAGE_DIR, exist_ok=True)

# Fetch API Keys securely from environment / GitHub Secrets
OMDB_API_KEY = os.getenv("OMDB_KEY", "")
BGG_API_TOKEN = os.getenv("BGG_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")


# -----------------------------------------------------------------------------
# 2. GITHUB SYNC HELPER FUNCTION
# -----------------------------------------------------------------------------
def push_csv_to_github(file_path, commit_message="Update inventory via app"):
    """Pushes local CSV changes directly back to GitHub repository."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        st.info("ℹ️ Local file saved. (Configure GITHUB_TOKEN & GITHUB_REPO secrets to auto-sync to GitHub)")
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
# 3. HELPER FUNCTIONS FOR CSV LOADING & SAVING
# -----------------------------------------------------------------------------
def safe_load_csv(file_path, expected_columns):
    """Safely loads CSV files without crashing on malformed lines."""
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


# --- BOARD GAME GEEK (BGG) API HELPERS ---
def fetch_bgg_game_matches(game_title):
    """Queries BoardGameGeek XML API2 with Bearer token authentication or fallback."""
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
        elif res.status_code == 401:
            st.error("BGG API Pending Auth Token (401). Use manual image URL entry for games in the meantime.")
    except Exception as e:
        st.error(f"BGG Fetch Error: {e}")
    return []


def fetch_bgg_game_details(bgg_id):
    """Fetches full game details and thumbnail for a specific BGG ID."""
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


# --- OMDB & MOVIE HELPERS ---
def fetch_collection_movies(collection_title):
    """Detects collection keywords and attempts to fetch individual film entries."""
    if not OMDB_API_KEY:
        return []
    
    clean_q = collection_title.lower()
    keywords = ["collection", "trilogy", "quadrilogy", "anthology", "series", "box set", "film set", "bundle", "franchise", "1-", "2-", "3-", "4-", "5-", "6-", "7-", "8-", "9-", "movie", "films"]
    search_term = clean_q
    for kw in keywords:
        search_term = search_term.replace(kw, "")
    search_term = search_term.strip()

    if not search_term:
        search_term = collection_title

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
    """Appends multiple movie dicts to the CSV at once and syncs with GitHub."""
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
    """Saves edited item attributes or handles deletion in the CSV."""
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
# 4. AUTHENTICATION
# -----------------------------------------------------------------------------
PIN_CODE = "1234"  # Change this to your preferred PIN code


def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("🏠 Home Inventory Access")
        input_pin = st.text_input(
            "Enter Invite Code / PIN:", type="password", key="pin_input"
        )
        if st.button("Login"):
            if input_pin == PIN_CODE:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Invalid invite code.")
        return False
    return True


if check_password():
    # -----------------------------------------------------------------------------
    # 5. TOP HEADER ACTION BAR (NO SIDEBAR)
    # -----------------------------------------------------------------------------
    header_col1, header_col2, header_col3 = st.columns([6, 2, 1.5])
    
    with header_col1:
        st.title("🏠 Home Inventory System")
        
    with header_col2:
        st.write("")
        if "show_add_form" not in st.session_state:
            st.session_state["show_add_form"] = False
            
        add_btn_label = "❌ Close Add Form" if st.session_state["show_add_form"] else "➕ Add New Item"
        if st.button(add_btn_label, use_container_width=True):
            st.session_state["show_add_form"] = not st.session_state["show_add_form"]
            st.rerun()

    with header_col3:
        st.write("")
        if st.button("🚪 Log Out", use_container_width=True):
            st.session_state["authenticated"] = False
            st.rerun()

    st.markdown("---")

    # -----------------------------------------------------------------------------
    # 6. ADD NEW ITEM DRAWER (EXPANDS ON "+ ADD NEW ITEM" BUTTON)
    # -----------------------------------------------------------------------------
    if st.session_state.get("show_add_form", False):
        with st.container(border=True):
            st.subheader("➕ Add New Inventory Item")

            category = st.selectbox(
                "Select Item Category",
                ["Movies & TV", "Board & Card Games", "Kitchen Gear"],
            )

            # --- CATEGORY 1: MOVIES & TV ---
            if category == "Movies & TV":
                st.subheader("Movie / TV Show / Collection Entry")

                if not OMDB_API_KEY:
                    st.warning("⚠️ `OMDB_KEY` secret is not set. You can manually enter movie details below.")

                col_search1, col_search2 = st.columns([3, 1])
                with col_search1:
                    search_title = st.text_input("Search Title or Collection", placeholder="e.g., The Dark Knight Trilogy, Star Wars, Avatar")
                with col_search2:
                    st.write("")
                    st.write("")
                    search_btn = st.button("🔍 Search Database")

                if search_btn and search_title:
                    if not OMDB_API_KEY:
                        st.error("Missing OMDb API Key in environment secrets.")
                    else:
                        with st.spinner(f"Searching for '{search_title}'..."):
                            collection_keywords = ["collection", "trilogy", "quadrilogy", "anthology", "series", "box set", "bundle", "franchise", "films"]
                            is_collection = any(kw in search_title.lower() for kw in collection_keywords)

                            if is_collection:
                                unpacked_movies = fetch_collection_movies(search_title)
                                if unpacked_movies:
                                    st.session_state["unpacked_collection"] = unpacked_movies
                                    st.session_state["search_results"] = []
                                    st.success(f"📦 Detected Collection! Found {len(unpacked_movies)} individual movies.")
                                else:
                                    is_collection = False

                            if not is_collection:
                                st.session_state.pop("unpacked_collection", None)
                                try:
                                    encoded_q = urllib.parse.quote_plus(search_title.strip())
                                    url = f"http://www.omdbapi.com/?s={encoded_q}&apikey={OMDB_API_KEY}"
                                    res = requests.get(url, timeout=5).json()

                                    if res.get("Response") == "True":
                                        st.session_state["search_results"] = res.get("Search", [])
                                        st.success(f"Found {len(st.session_state['search_results'])} match(es)!")
                                    else:
                                        st.session_state["search_results"] = []
                                        st.error(f"No results found for '{search_title}'.")
                                except Exception as e:
                                    st.error(f"Error fetching search results: {e}")

                if st.session_state.get("unpacked_collection"):
                    st.markdown("---")
                    st.markdown("#### 📦 Collection Unpacking Options")
                    unpacked_list = st.session_state["unpacked_collection"]

                    st.write("The following movies were found in this collection:")
                    col_grid = st.columns(min(len(unpacked_list), 4))
                    for i, m_item in enumerate(unpacked_list):
                        c = col_grid[i % 4]
                        with c:
                            if m_item["Image_Path"]:
                                st.image(m_item["Image_Path"], width=90)
                            st.caption(f"**{m_item['Title']}** ({m_item['Year Released']})")

                    col_u1, col_u2 = st.columns([1, 1])
                    with col_u1:
                        if st.button("🚀 Add All Individual Movies to Inventory"):
                            if save_multiple_movies_to_csv("movies_and_tv_collection.csv", unpacked_list):
                                st.success(f"Successfully added {len(unpacked_list)} movies from the collection!")
                                st.session_state.pop("unpacked_collection", None)
                                st.session_state["show_add_form"] = False
                                st.rerun()

                    with col_u2:
                        if st.button("📦 Add as Single Collection Entry Instead"):
                            st.session_state["m_title"] = search_title
                            st.session_state.pop("unpacked_collection", None)

                if st.session_state.get("search_results"):
                    st.markdown("---")
                    options = {f"{m['Title']} ({m.get('Year', 'N/A')}) [{m.get('Type', '').capitalize()}]": m["imdbID"] for m in st.session_state["search_results"]}
                    selected_label = st.selectbox("Choose match:", list(options.keys()))
                    selected_imdb_id = options[selected_label]

                    if selected_imdb_id:
                        detail_url = f"http://www.omdbapi.com/?i={selected_imdb_id}&apikey={OMDB_API_KEY}"
                        full_res = requests.get(detail_url, timeout=5).json()

                        if full_res.get("Response") == "True":
                            if st.button("✅ Accept & Use This Movie"):
                                st.session_state["m_title"] = full_res.get("Title", "")
                                st.session_state["m_year"] = full_res.get("Year", "")
                                st.session_state["m_rating"] = full_res.get("Rated", "")
                                st.session_state["m_length"] = full_res.get("Runtime", "")
                                st.session_state["m_type"] = full_res.get("Type", "movie").capitalize()
                                st.session_state["m_genre"] = full_res.get("Genre", "")
                                st.session_state["m_poster"] = full_res.get("Poster", "") if full_res.get("Poster") != "N/A" else ""

                with st.form("movie_form", clear_on_submit=True):
                    title = st.text_input("Title *", value=st.session_state.get("m_title", ""))
                    rating = st.text_input("Rating", value=st.session_state.get("m_rating", ""))
                    year = st.text_input("Year Released", value=st.session_state.get("m_year", ""))
                    length = st.text_input("Length of Movie", value=st.session_state.get("m_length", ""))
                    m_type = st.selectbox("Type", ["Movie", "TV", "Collection"], index=0)
                    genre = st.text_input("Genre", value=st.session_state.get("m_genre", ""))
                    poster_link = st.text_input("Poster / Image URL", value=st.session_state.get("m_poster", ""))
                    uploaded_image = st.file_uploader("Upload Custom Image File", type=["jpg", "png", "jpeg"])

                    if st.form_submit_button("Save Movie to Inventory"):
                        if title:
                            image_path = poster_link
                            if uploaded_image:
                                image_path = os.path.join(IMAGE_DIR, uploaded_image.name)
                                with open(image_path, "wb") as f:
                                    f.write(uploaded_image.getbuffer())

                            new_entry = {"Title": title, "Rating": rating, "Year Released": year, "Length of Movie": length, "Type": m_type, "Genre": genre, "Image_Path": image_path}
                            file_path = "movies_and_tv_collection.csv"
                            existing_df = safe_load_csv(file_path, ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
                            updated_df = pd.concat([existing_df, pd.DataFrame([new_entry])], ignore_index=True)
                            updated_df.to_csv(file_path, index=False)
                            push_csv_to_github(file_path, f"Add movie '{title}'")

                            st.success(f"Added '{title}' to Movies & TV database!")
                            st.session_state["show_add_form"] = False
                            st.rerun()

            # --- CATEGORY 2: BOARD & CARD GAMES ---
            elif category == "Board & Card Games":
                st.subheader("Board & Card Game Entry")
                col_gsearch1, col_gsearch2 = st.columns([3, 1])
                with col_gsearch1:
                    g_search_q = st.text_input("Search Game Title", placeholder="e.g., Catan, Ticket to Ride, Wingspan")
                with col_gsearch2:
                    st.write("")
                    st.write("")
                    if st.button("🔍 Search BGG"):
                        if g_search_q:
                            bgg_results = fetch_bgg_game_matches(g_search_q)
                            if bgg_results:
                                st.session_state["bgg_search_results"] = bgg_results

                if st.session_state.get("bgg_search_results"):
                    bgg_opts = {f"{g['name']} ({g['year']})": g["id"] for g in st.session_state["bgg_search_results"]}
                    selected_g_label = st.selectbox("Choose game match:", list(bgg_opts.keys()))
                    selected_bgg_id = bgg_opts[selected_g_label]

                    if selected_bgg_id and st.button("✅ Accept & Auto-Fill Game Specs"):
                        details = fetch_bgg_game_details(selected_bgg_id)
                        st.session_state["g_title"] = selected_g_label.split(" (")[0]
                        st.session_state["g_players"] = details.get("Number of Players", "")
                        st.session_state["g_length"] = details.get("Length of Play", "")
                        st.session_state["g_age"] = details.get("Age Rating", "")
                        st.session_state["g_image"] = details.get("Image_Path", "")

                with st.form("game_form", clear_on_submit=True):
                    title = st.text_input("Game Title *", value=st.session_state.get("g_title", ""))
                    players = st.text_input("Number of Players", value=st.session_state.get("g_players", ""))
                    length = st.text_input("Length of Play", value=st.session_state.get("g_length", ""))
                    age = st.text_input("Age Rating", value=st.session_state.get("g_age", ""))
                    style = st.text_input("Style of Game (Board, Card, Dice)")
                    box_photo_url = st.text_input("Box Photo URL Link", value=st.session_state.get("g_image", ""))
                    uploaded_image = st.file_uploader("Upload Box Photo File", type=["jpg", "png", "jpeg"])

                    if st.form_submit_button("Save Game to Inventory"):
                        if title:
                            image_path = box_photo_url
                            if uploaded_image:
                                image_path = os.path.join(IMAGE_DIR, uploaded_image.name)
                                with open(image_path, "wb") as f:
                                    f.write(uploaded_image.getbuffer())

                            new_entry = {"Title": title, "Number of Players": players, "Length of Play": length, "Age Rating": age, "Style of Game": style, "Image_Path": image_path}
                            file_path = "board_and_card_games_collection.csv"
                            existing_df = safe_load_csv(file_path, ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
                            updated_df = pd.concat([existing_df, pd.DataFrame([new_entry])], ignore_index=True)
                            updated_df.to_csv(file_path, index=False)
                            push_csv_to_github(file_path, f"Add game '{title}'")

                            st.success(f"Added '{title}' to Games database!")
                            st.session_state["show_add_form"] = False
                            st.rerun()

            # --- CATEGORY 3: KITCHEN GEAR ---
            elif category == "Kitchen Gear":
                st.subheader("Kitchen Gear Entry")
                with st.form("kitchen_form", clear_on_submit=True):
                    title = st.text_input("Name of Item *")
                    eq_type = st.selectbox("Type of Equipment", ["Appliance", "Cookware", "Appliance Accessory", "Utensil"])
                    manual = st.text_input("Instruction Manual Link (URL)")
                    image_url = st.text_input("Item Photo Image URL")
                    uploaded_image = st.file_uploader("Upload Item Photo File", type=["jpg", "png", "jpeg"])

                    if st.form_submit_button("Save Kitchen Gear"):
                        if title:
                            image_path = image_url
                            if uploaded_image:
                                image_path = os.path.join(IMAGE_DIR, uploaded_image.name)
                                with open(image_path, "wb") as f:
                                    f.write(uploaded_image.getbuffer())

                            new_entry = {"Name of Item": title, "Type of Equipment": eq_type, "Instruction Manual Link": manual, "Image_Path": image_path}
                            file_path = "kitchen_gear_inventory_v2.csv"
                            existing_df = safe_load_csv(file_path, ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"])
                            updated_df = pd.concat([existing_df, pd.DataFrame([new_entry])], ignore_index=True)
                            updated_df.to_csv(file_path, index=False)
                            push_csv_to_github(file_path, f"Add kitchen item '{title}'")

                            st.success(f"Added '{title}' to Kitchen Gear database!")
                            st.session_state["show_add_form"] = False
                            st.rerun()

        st.markdown("---")

    # -----------------------------------------------------------------------------
    # 7. MASTER INVENTORY SEARCH & TAB SYSTEM
    # -----------------------------------------------------------------------------
    df_movies = safe_load_csv("movies_and_tv_collection.csv", ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
    df_games = safe_load_csv("board_and_card_games_collection.csv", ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
    df_kitchen = safe_load_csv("kitchen_gear_inventory_v2.csv", ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"])

    # Master Top Control Bar
    with st.container(border=True):
        col_search, col_sort, col_order, col_view = st.columns([3.5, 2, 1.5, 2])
        with col_search:
            global_search_q = st.text_input("🔍 Global Search (All Databases)...", key="master_search_bar")
        with col_sort:
            sort_by_col = st.selectbox("Sort By:", ["Title / Item Name", "Category"], key="master_sort_select")
        with col_order:
            order_by = st.radio("Order:", ["Asc", "Desc"], horizontal=True, key="master_order_radio")
        with col_view:
            layout_view = st.radio("Layout View:", ["📋 List", "🎴 Cards"], horizontal=True, key="master_view_radio")

    # Main Category Navigation Tabs
    tab_all, tab_movies, tab_games, tab_kitchen = st.tabs([
        "🌐 All Items (Master)",
        "🎬 Movies & TV",
        "🎲 Board & Card Games",
        "🍳 Kitchen Gear"
    ])

    def render_edit_form(unique_key_id, item_id, row, editable_cols, file_path, title_col):
        """Universal form renderer for editing and deleting items."""
        edit_inputs = {}
        for col_name in editable_cols:
            input_key = f"edit_field_{unique_key_id}_{col_name}"
            if input_key not in st.session_state:
                st.session_state[input_key] = str(row.get(col_name, ""))

            edit_inputs[col_name] = st.text_input(f"{col_name}", key=input_key)

        col_btn1, col_btn2 = st.columns([1, 1])
        with col_btn1:
            if st.button("💾 Save Changes", key=f"save_{unique_key_id}"):
                if save_edited_row(file_path, item_id, edit_inputs, title_col):
                    st.session_state[f"expand_edit_{unique_key_id}"] = False
                    for col_name in editable_cols:
                        st.session_state.pop(f"edit_field_{unique_key_id}_{col_name}", None)
                    st.rerun()

        with col_btn2:
            if st.button("🗑️ Delete Item", key=f"del_{unique_key_id}"):
                if save_edited_row(file_path, item_id, {"_DELETE_": True}, title_col):
                    st.session_state[f"expand_edit_{unique_key_id}"] = False
                    for col_name in editable_cols:
                        st.session_state.pop(f"edit_field_{unique_key_id}_{col_name}", None)
                    st.rerun()

    def display_items_list_or_cards(df, title_col, details_func, summary_inline_func, file_path, editable_cols, category_badge="", key_prefix="tab"):
        if df.empty:
            st.info("No items in this selection.")
            return

        if global_search_q:
            mask = df[title_col].astype(str).str.contains(global_search_q, case=False)
            df = df[mask]

        if df.empty:
            st.info("No items match your search query.")
            return

        is_asc = order_by == "Asc"
        if sort_by_col == "Title / Item Name" and title_col in df.columns:
            df = df.sort_values(by=title_col, ascending=is_asc, key=lambda x: x.astype(str).str.lower())
        elif sort_by_col == "Category" and "_Category" in df.columns:
            df = df.sort_values(by="_Category", ascending=is_asc)

        df_reset = df.reset_index(drop=True)

        # CARDS VIEW
        if layout_view == "🎴 Cards":
            cols = st.columns(3)
            for idx, row in df_reset.iterrows():
                col = cols[idx % 3]
                item_id = str(row[title_col])
                row_file_path = str(row.get("_File", file_path))
                row_badge = str(row.get("_Category", category_badge))
                unique_key_id = f"{key_prefix}_{idx}_{hash(item_id)}"

                with col:
                    with st.container(border=True):
                        img_val = row.get("Image_Path", "")
                        if pd.notna(img_val) and str(img_val).strip() != "":
                            st.image(str(img_val), use_container_width=True)
                        else:
                            st.caption("📷 No image available")

                        if row_badge:
                            st.caption(f"**Category:** {row_badge}")
                        st.subheader(item_id)
                        st.write(details_func(row))

                        with st.expander(f"✏️ Edit / Delete '{item_id}'"):
                            render_edit_form(unique_key_id, item_id, row, editable_cols, row_file_path, title_col)

        # LIST VIEW
        else:
            for idx, row in df_reset.iterrows():
                item_id = str(row[title_col])
                row_file_path = str(row.get("_File", file_path))
                row_badge = str(row.get("_Category", category_badge))
                unique_key_id = f"{key_prefix}_{idx}_{hash(item_id)}"
                
                with st.container(border=True):
                    c_img, c_info, c_edit = st.columns([0.4, 7.8, 0.8], vertical_alignment="center")

                    with c_img:
                        img_val = row.get("Image_Path", "")
                        if pd.notna(img_val) and str(img_val).strip() != "":
                            st.image(str(img_val), width=32)
                        else:
                            st.caption("📷")

                    with c_info:
                        inline_details = summary_inline_func(row)
                        badge_str = f"<strong>[{row_badge}]</strong> " if row_badge else ""
                        st.markdown(
                            f"<div style='margin:0; padding:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;'>"
                            f"{badge_str}<strong>{item_id}</strong> &nbsp;|&nbsp; "
                            f"<span style='color:#a0a0a0; font-size:0.9em;'>{inline_details}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    with c_edit:
                        expander_key = f"expand_edit_{unique_key_id}"
                        if expander_key not in st.session_state:
                            st.session_state[expander_key] = False

                        if st.button("✏️ Edit", key=f"btn_toggle_edit_{unique_key_id}"):
                            st.session_state[expander_key] = not st.session_state[expander_key]

                    if st.session_state.get(expander_key, False):
                        st.markdown("---")
                        st.subheader(f"✏️ Editing: {item_id}")
                        render_edit_form(unique_key_id, item_id, row, editable_cols, row_file_path, title_col)

    # --- TAB 1: MASTER ALL ITEMS TAB ---
    with tab_all:
        st.subheader("🌐 Master Inventory View")
        
        # Prepare merged collection dataframe
        m_df = df_movies.copy()
        m_df["_Name"] = m_df["Title"]
        m_df["_Category"] = "Movies & TV"
        m_df["_File"] = "movies_and_tv_collection.csv"

        g_df = df_games.copy()
        g_df["_Name"] = g_df["Title"]
        g_df["_Category"] = "Board & Card Games"
        g_df["_File"] = "board_and_card_games_collection.csv"

        k_df = df_kitchen.copy()
        k_df["_Name"] = k_df["Name of Item"]
        k_df["_Category"] = "Kitchen Gear"
        k_df["_File"] = "kitchen_gear_inventory_v2.csv"

        master_df = pd.concat([m_df, g_df, k_df], ignore_index=True)

        def master_details(r):
            cat = r.get("_Category", "")
            if cat == "Movies & TV":
                return f"**Type:** {r.get('Type', '')} | **Rating:** {r.get('Rating', '')}\n\n**Year:** {r.get('Year Released', '')} | **Genre:** {r.get('Genre', '')}"
            elif cat == "Board & Card Games":
                return f"**Players:** {r.get('Number of Players', '')}\n\n**Length:** {r.get('Length of Play', '')} | **Age:** {r.get('Age Rating', '')}"
            else:
                return f"**Type:** {r.get('Type of Equipment', '')}"

        def master_summary(r):
            cat = r.get("_Category", "")
            if cat == "Movies & TV":
                return f"Type: {r.get('Type', '')} | Year: {r.get('Year Released', '')} | Genre: {r.get('Genre', '')}"
            elif cat == "Board & Card Games":
                return f"Players: {r.get('Number of Players', '')} | Length: {r.get('Length of Play', '')} | Age: {r.get('Age Rating', '')}"
            else:
                return f"Type: {r.get('Type of Equipment', '')}"

        display_items_list_or_cards(
            master_df,
            "_Name",
            master_details,
            master_summary,
            "movies_and_tv_collection.csv",
            ["Title", "Name of Item", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Type of Equipment", "Instruction Manual Link", "Image_Path"],
            key_prefix="master"
        )

    # --- TAB 2: MOVIES & TV ---
    with tab_movies:
        st.subheader("🎬 Movies & TV Collection")
        display_items_list_or_cards(
            df_movies,
            "Title",
            lambda r: f"**Type:** {r.get('Type', '')} | **Rating:** {r.get('Rating', '')}\n\n**Year:** {r.get('Year Released', '')} | **Genre:** {r.get('Genre', '')}",
            lambda r: f"Type: {r.get('Type', '')} | Rating: {r.get('Rating', '')} | Year: {r.get('Year Released', '')} | Genre: {r.get('Genre', '')}",
            "movies_and_tv_collection.csv",
            ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"],
            key_prefix="movies"
        )

    # --- TAB 3: BOARD & CARD GAMES ---
    with tab_games:
        st.subheader("🎲 Board & Card Games Collection")
        display_items_list_or_cards(
            df_games,
            "Title",
            lambda r: f"**Players:** {r.get('Number of Players', '')}\n\n**Length:** {r.get('Length of Play', '')} | **Age:** {r.get('Age Rating', '')}",
            lambda r: f"Players: {r.get('Number of Players', '')} | Length: {r.get('Length of Play', '')} | Age: {r.get('Age Rating', '')} | Style: {r.get('Style of Game', '')}",
            "board_and_card_games_collection.csv",
            ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"],
            key_prefix="games"
        )

    # --- TAB 4: KITCHEN GEAR ---
    with tab_kitchen:
        st.subheader("🍳 Kitchen Gear Inventory")
        display_items_list_or_cards(
            df_kitchen,
            "Name of Item",
            lambda r: f"**Type:** {r.get('Type of Equipment', '')}\n\n" + (f"[📄 Manual Link]({r['Instruction Manual Link']})" if pd.notna(r.get("Instruction Manual Link")) and str(r.get("Instruction Manual Link")).startswith("http") else ""),
            lambda r: f"Type: {r.get('Type of Equipment', '')} " + (f"| [📄 Manual Link]({r['Instruction Manual Link']})" if pd.notna(r.get("Instruction Manual Link")) and str(r.get("Instruction Manual Link")).startswith("http") else ""),
            "kitchen_gear_inventory_v2.csv",
            ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"],
            key_prefix="kitchen"
        )
