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

# Custom CSS for compact list spacing
st.markdown(
    """
    <style>
    div[data-testid="stVerticalBlock"] > div {
        gap: 0.2rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Directory for storing user-uploaded images locally
IMAGE_DIR = "uploaded_images"
os.makedirs(IMAGE_DIR, exist_ok=True)

# Fetch API Keys securely from environment / GitHub Secrets
OMDB_API_KEY = os.getenv("OMDB_KEY", "")
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
# 3. HELPER FUNCTIONS FOR RESILIENT CSV LOADING & SAVING
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


# --- BOARD GAME GEEK (BGG) API HELPERS (FIXED) ---
def fetch_bgg_game_matches(game_title):
    """Queries BoardGameGeek free API for game titles with custom User-Agent headers."""
    try:
        encoded_q = urllib.parse.quote_plus(game_title.strip())
        url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame"
        headers = {"User-Agent": "HomeInventoryApp/1.0 (Streamlit App)"}
        
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = []
            for item in root.findall("item")[:6]:  # Top 6 matches
                bgg_id = item.attrib.get("id")
                name_elem = item.find("name")
                name = name_elem.attrib.get("value") if name_elem is not None else game_title
                year_elem = item.find("yearpublished")
                year = year_elem.attrib.get("value") if year_elem is not None else ""
                items.append({"id": bgg_id, "name": name, "year": year})
            return items
        else:
            st.error(f"BGG Error Code {res.status_code}: Service blocked or throttled.")
    except Exception as e:
        st.error(f"BGG Search Error: {e}")
    return []


def fetch_bgg_game_details(bgg_id):
    """Fetches details (thumbnail, player count, playtime, age) for a BGG ID."""
    try:
        url = f"https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}"
        headers = {"User-Agent": "HomeInventoryApp/1.0 (Streamlit App)"}
        res = requests.get(url, headers=headers, timeout=6)
        
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            item = root.find("item")
            if item is not None:
                # Thumbnail / Image
                image_elem = item.find("thumbnail")
                if image_elem is None:
                    image_elem = item.find("image")
                image_url = image_elem.text if image_elem is not None else ""
                
                # Players
                min_p = item.find("minplayers").attrib.get("value") if item.find("minplayers") is not None else ""
                max_p = item.find("maxplayers").attrib.get("value") if item.find("maxplayers") is not None else ""
                players = f"{min_p}-{max_p} Players" if min_p and max_p and min_p != max_p else f"{min_p} Players"

                # Playtime
                min_t = item.find("minplaytime").attrib.get("value") if item.find("minplaytime") is not None else ""
                max_t = item.find("maxplaytime").attrib.get("value") if item.find("maxplaytime") is not None else ""
                length = f"{min_t}-{max_t} min" if min_t and max_t and min_t != max_t else f"{min_t} min"

                # Age
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
        st.error(f"BGG Detail Error: {e}")
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
PIN_CODE = "1234"  # Change this to your preferred PIN or invite code


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
    # 5. NAVIGATION & LOGOUT
    # -----------------------------------------------------------------------------
    st.sidebar.title("Navigation")
    app_mode = st.sidebar.radio(
        "Select Page", ["🔍 Browse Inventory", "➕ Add New Item"]
    )

    if GITHUB_TOKEN and GITHUB_REPO:
        st.sidebar.caption(f"🟢 Connected to GitHub: `{GITHUB_REPO}`")

    if st.sidebar.button("Log Out"):
        st.session_state["authenticated"] = False
        st.rerun()

    # -----------------------------------------------------------------------------
    # 6. PAGE: ADD NEW ITEM
    # -----------------------------------------------------------------------------
    if app_mode == "➕ Add New Item":
        st.title("➕ Add New Inventory Item")

        category = st.selectbox(
            "Select Item Category",
            ["Movies & TV", "Board & Card Games", "Kitchen Gear"],
        )

        # --- CATEGORY 1: MOVIES & TV ---
        if category == "Movies & TV":
            st.subheader("Movie / TV Show / Collection Entry")

            if not OMDB_API_KEY:
                st.warning("⚠️ `OMDB_KEY` secret is not set. You can manually enter movie details below.")

            st.markdown("#### 1. Search Movie Database or Collection")
            col_search1, col_search2 = st.columns([3, 1])
            with col_search1:
                search_title = st.text_input(
                    "Search Title or Collection",
                    placeholder="e.g., The Dark Knight Trilogy, Star Wars, Avatar",
                )
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
                            st.rerun()

                with col_u2:
                    if st.button("📦 Add as Single Collection Entry Instead"):
                        st.session_state["m_title"] = search_title
                        st.session_state.pop("unpacked_collection", None)
                        st.info("Switched to single entry form below.")

            if st.session_state.get("search_results"):
                st.markdown("---")
                st.markdown("#### 2. Select the Correct Match")

                options = {
                    f"{m['Title']} ({m.get('Year', 'N/A')}) [{m.get('Type', '').capitalize()}]": m["imdbID"]
                    for m in st.session_state["search_results"]
                }

                selected_label = st.selectbox("Choose from search results:", list(options.keys()))
                selected_imdb_id = options[selected_label]

                if selected_imdb_id:
                    detail_url = f"http://www.omdbapi.com/?i={selected_imdb_id}&apikey={OMDB_API_KEY}"
                    full_res = requests.get(detail_url, timeout=5).json()

                    if full_res.get("Response") == "True":
                        col_preview1, col_preview2 = st.columns([1, 3])

                        with col_preview1:
                            poster = full_res.get("Poster", "")
                            if poster and poster != "N/A":
                                st.image(poster, caption="Movie Poster", use_container_width=True)
                            else:
                                st.caption("📷 No Poster Available")

                        with col_preview2:
                            st.subheader(f"{full_res.get('Title')} ({full_res.get('Year')})")
                            st.markdown(f"**Type:** {full_res.get('Type', '').capitalize()} | **Rated:** {full_res.get('Rated')}")
                            st.markdown(f"**Runtime:** {full_res.get('Runtime')} | **Genre:** {full_res.get('Genre')}")
                            st.write(f"**Plot:** {full_res.get('Plot', 'N/A')}")

                            if st.button("✅ Accept & Use This Movie"):
                                st.session_state["m_title"] = full_res.get("Title", "")
                                st.session_state["m_year"] = full_res.get("Year", "")
                                st.session_state["m_rating"] = full_res.get("Rated", "")
                                st.session_state["m_length"] = full_res.get("Runtime", "")
                                st.session_state["m_type"] = full_res.get("Type", "movie").capitalize()
                                st.session_state["m_genre"] = full_res.get("Genre", "")
                                st.session_state["m_poster"] = poster if poster != "N/A" else ""
                                st.success(f"Loaded '{full_res.get('Title')}' into form below!")

            st.markdown("---")
            st.markdown("#### 3. Verify & Save Entry")

            with st.form("movie_form", clear_on_submit=True):
                title = st.text_input("Title *", value=st.session_state.get("m_title", ""))
                rating = st.text_input("Rating (PG, PG-13, R)", value=st.session_state.get("m_rating", ""))
                year = st.text_input("Year Released", value=st.session_state.get("m_year", ""))
                length = st.text_input("Length of Movie", value=st.session_state.get("m_length", ""))
                m_type = st.selectbox("Type", ["Movie", "TV", "Collection"], index=(0 if st.session_state.get("m_type", "Movie") == "Movie" else 1))
                genre = st.text_input("Genre", value=st.session_state.get("m_genre", ""))
                poster_link = st.text_input("Poster / Image URL", value=st.session_state.get("m_poster", ""))

                uploaded_image = st.file_uploader("Or Upload Custom Image File", type=["jpg", "png", "jpeg"])

                if st.form_submit_button("Save Movie to Inventory"):
                    if not title:
                        st.error("Title is required.")
                    else:
                        image_path = poster_link
                        if uploaded_image:
                            image_path = os.path.join(IMAGE_DIR, uploaded_image.name)
                            with open(image_path, "wb") as f:
                                f.write(uploaded_image.getbuffer())

                        new_entry = {
                            "Title": title,
                            "Rating": rating,
                            "Year Released": year,
                            "Length of Movie": length,
                            "Type": m_type,
                            "Genre": genre,
                            "Image_Path": image_path,
                        }

                        file_path = "movies_and_tv_collection.csv"
                        existing_df = safe_load_csv(file_path, ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
                        updated_df = pd.concat([existing_df, pd.DataFrame([new_entry])], ignore_index=True)
                        updated_df.to_csv(file_path, index=False)
                        push_csv_to_github(file_path, f"Add movie '{title}'")

                        st.success(f"Added '{title}' to Movies & TV database!")

                        for key in ["m_title", "m_year", "m_rating", "m_length", "m_type", "m_genre", "m_poster", "search_results", "unpacked_collection"]:
                            st.session_state.pop(key, None)

        # --- CATEGORY 2: BOARD & CARD GAMES (WITH BGG SEARCH) ---
        elif category == "Board & Card Games":
            st.subheader("Board & Card Game Entry")

            st.markdown("#### 1. Search BoardGameGeek Database")
            col_gsearch1, col_gsearch2 = st.columns([3, 1])
            with col_gsearch1:
                g_search_q = st.text_input("Search Game Title", placeholder="e.g., Catan, Ticket to Ride, Wingspan")
            with col_gsearch2:
                st.write("")
                st.write("")
                if st.button("🔍 Search BGG"):
                    if g_search_q:
                        with st.spinner(f"Searching BoardGameGeek for '{g_search_q}'..."):
                            bgg_results = fetch_bgg_game_matches(g_search_q)
                            if bgg_results:
                                st.session_state["bgg_search_results"] = bgg_results
                                st.success(f"Found {len(bgg_results)} game match(es)!")
                            else:
                                st.error("No games found on BGG. Enter details manually below.")

            if st.session_state.get("bgg_search_results"):
                bgg_opts = {f"{g['name']} ({g['year']})": g["id"] for g in st.session_state["bgg_search_results"]}
                selected_g_label = st.selectbox("Choose game match:", list(bgg_opts.keys()))
                selected_bgg_id = bgg_opts[selected_g_label]

                if selected_bgg_id and st.button("✅ Accept & Auto-Fill Game Metadata"):
                    details = fetch_bgg_game_details(selected_bgg_id)
                    st.session_state["g_title"] = selected_g_label.split(" (")[0]
                    st.session_state["g_players"] = details.get("Number of Players", "")
                    st.session_state["g_length"] = details.get("Length of Play", "")
                    st.session_state["g_age"] = details.get("Age Rating", "")
                    st.session_state["g_image"] = details.get("Image_Path", "")
                    st.success("Loaded game metadata into form below!")

            st.markdown("---")
            st.markdown("#### 2. Verify & Save Entry")

            with st.form("game_form", clear_on_submit=True):
                title = st.text_input("Game Title *", value=st.session_state.get("g_title", ""))
                players = st.text_input("Number of Players", value=st.session_state.get("g_players", ""))
                length = st.text_input("Length of Play", value=st.session_state.get("g_length", ""))
                age = st.text_input("Age Rating", value=st.session_state.get("g_age", ""))
                style = st.text_input("Style of Game (Board, Card, Dice)")
                box_photo_url = st.text_input("Box Photo URL / Poster Link", value=st.session_state.get("g_image", ""))
                uploaded_image = st.file_uploader("Or Upload Box Photo File", type=["jpg", "png", "jpeg"])

                if st.form_submit_button("Save Game to Inventory"):
                    if not title:
                        st.error("Game title is required.")
                    else:
                        image_path = box_photo_url
                        if uploaded_image:
                            image_path = os.path.join(IMAGE_DIR, uploaded_image.name)
                            with open(image_path, "wb") as f:
                                f.write(uploaded_image.getbuffer())

                        new_entry = {
                            "Title": title,
                            "Number of Players": players,
                            "Length of Play": length,
                            "Age Rating": age,
                            "Style of Game": style,
                            "Image_Path": image_path,
                        }
                        file_path = "board_and_card_games_collection.csv"
                        existing_df = safe_load_csv(file_path, ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
                        updated_df = pd.concat([existing_df, pd.DataFrame([new_entry])], ignore_index=True)
                        updated_df.to_csv(file_path, index=False)
                        push_csv_to_github(file_path, f"Add game '{title}'")
                        st.success(f"Added '{title}' to Games database!")

                        for key in ["g_title", "g_players", "g_length", "g_age", "g_image", "bgg_search_results"]:
                            st.session_state.pop(key, None)

        # --- CATEGORY 3: KITCHEN GEAR ---
        elif category == "Kitchen Gear":
            st.subheader("Kitchen Gear Entry")
            with st.form("kitchen_form", clear_on_submit=True):
                title = st.text_input("Name of Item *")
                eq_type = st.selectbox("Type of Equipment", ["Appliance", "Cookware", "Appliance Accessory", "Utensil"])
                manual = st.text_input("Instruction Manual Link (URL)")
                image_url = st.text_input("Item Photo Image URL")
                uploaded_image = st.file_uploader("Or Upload Item Photo File", type=["jpg", "png", "jpeg"])

                if st.form_submit_button("Save Kitchen Gear"):
                    if not title:
                        st.error("Item name is required.")
                    else:
                        image_path = image_url
                        if uploaded_image:
                            image_path = os.path.join(IMAGE_DIR, uploaded_image.name)
                            with open(image_path, "wb") as f:
                                f.write(uploaded_image.getbuffer())

                        new_entry = {
                            "Name of Item": title,
                            "Type of Equipment": eq_type,
                            "Instruction Manual Link": manual,
                            "Image_Path": image_path,
                        }
                        file_path = "kitchen_gear_inventory_v2.csv"
                        existing_df = safe_load_csv(file_path, ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"])
                        updated_df = pd.concat([existing_df, pd.DataFrame([new_entry])], ignore_index=True)
                        updated_df.to_csv(file_path, index=False)
                        push_csv_to_github(file_path, f"Add kitchen item '{title}'")
                        st.success(f"Added '{title}' to Kitchen Gear database!")

    # -----------------------------------------------------------------------------
    # 7. PAGE: BROWSE INVENTORY WITH TAB-SPECIFIC METADATA / IMAGE AUTO-FILL
    # -----------------------------------------------------------------------------
    elif app_mode == "🔍 Browse Inventory":
        st.title("🍊 Browse Home Inventory")

        df_movies = safe_load_csv("movies_and_tv_collection.csv", ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"])
        df_games = safe_load_csv("board_and_card_games_collection.csv", ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
        df_kitchen = safe_load_csv("kitchen_gear_inventory_v2.csv", ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"])

        active_category = st.radio(
            "Select Inventory Category:",
            ["Movies & TV", "Board & Card Games", "Kitchen Gear"],
            horizontal=True,
            key="active_category_radio"
        )

        if active_category == "Movies & TV":
            sort_options = ["Title", "Year Released", "Rating", "Genre", "Type"]
        elif active_category == "Board & Card Games":
            sort_options = ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game"]
        else:
            sort_options = ["Name of Item", "Type of Equipment"]

        # --- EXPLICIT TOP CONTROL BAR CONTAINER ---
        with st.container(border=True):
            col_search, col_sort, col_order, col_view = st.columns([3, 2, 1.5, 2])
            with col_search:
                global_search_q = st.text_input("🔍 Search items...", key="main_search_bar")
            with col_sort:
                sort_by_col = st.selectbox("Sort By:", sort_options, key="main_sort_select")
            with col_order:
                order_by = st.radio("Order:", ["Asc", "Desc"], horizontal=True, key="main_order_radio")
            with col_view:
                layout_view = st.radio("Layout View:", ["🎴 Cards", "📋 List"], horizontal=True, key="main_view_radio")

        st.markdown("---")

        def display_inventory_items(df, title_col, details_func, summary_inline_func, file_path, editable_cols, category_type="movie", image_col="Image_Path"):
            if df.empty:
                st.info("No items in this category yet.")
                return

            if global_search_q:
                mask = df[title_col].astype(str).str.contains(global_search_q, case=False)
                df = df[mask]

            if df.empty:
                st.info("No items matching your search.")
                return

            is_asc = order_by == "Asc"
            if sort_by_col in df.columns:
                df = df.sort_values(by=sort_by_col, ascending=is_asc, key=lambda x: x.astype(str).str.lower())

            # --- 🎴 CARDS VIEW ---
            if layout_view == "🎴 Cards":
                cols = st.columns(3)
                for idx, row in df.reset_index(drop=True).iterrows():
                    col = cols[idx % 3]
                    item_id = str(row[title_col])
                    with col:
                        with st.container(border=True):
                            img_val = row.get(image_col, "")
                            if pd.notna(img_val) and str(img_val).strip() != "":
                                st.image(str(img_val), use_container_width=True)
                            else:
                                st.caption("📷 No image available")

                            st.subheader(item_id)
                            st.write(details_func(row))

                            with st.expander(f"✏️ Edit / Delete '{item_id}'"):
                                render_edit_form(idx, item_id, row, editable_cols, file_path, title_col, category_type)

            # --- 📋 LIST VIEW: RIGHT EDIT EXPANDER WITH FULL-WIDTH BELOW DRAWER ---
            else:
                for idx, row in df.reset_index(drop=True).iterrows():
                    item_id = str(row[title_col])
                    
                    with st.container(border=True):
                        c_img, c_info, c_edit = st.columns([0.4, 7.8, 0.8], vertical_alignment="center")

                        with c_img:
                            img_val = row.get(image_col, "")
                            if pd.notna(img_val) and str(img_val).strip() != "":
                                st.image(str(img_val), width=32)
                            else:
                                st.caption("📷")

                        with c_info:
                            inline_details = summary_inline_func(row)
                            st.markdown(
                                f"<div style='margin:0; padding:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;'>"
                                f"<strong>{item_id}</strong> &nbsp;|&nbsp; "
                                f"<span style='color:#a0a0a0; font-size:0.9em;'>{inline_details}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                        with c_edit:
                            expander_key = f"expand_edit_{file_path}_{idx}"
                            if expander_key not in st.session_state:
                                st.session_state[expander_key] = False

                            if st.button("✏️ Edit", key=f"btn_toggle_edit_{file_path}_{idx}"):
                                st.session_state[expander_key] = not st.session_state[expander_key]

                        if st.session_state.get(expander_key, False):
                            st.markdown("---")
                            st.subheader(f"✏️ Editing: {item_id}")
                            render_edit_form(idx, item_id, row, editable_cols, file_path, title_col, category_type)

        def render_edit_form(idx, item_id, row, editable_cols, file_path, title_col, category_type):
            """Universal form renderer supporting single edits, metadata search, and collection splitting."""
            
            # --- 🎬 MOVIE METADATA SEARCH ---
            if category_type == "movie" and OMDB_API_KEY:
                st.markdown("##### 🔍 Search Movie Metadata / Split Collection")
                col_m1, col_m2 = st.columns([3, 1])
                with col_m1:
                    edit_search_q = st.text_input("Search Title or Collection Query", value=item_id, key=f"edit_search_q_{file_path}_{idx}")
                with col_m2:
                    st.write("")
                    st.write("")
                    if st.button("Fetch / Unpack", key=f"btn_edit_search_{file_path}_{idx}"):
                        try:
                            clean_q = edit_search_q.strip()
                            collection_keywords = ["collection", "trilogy", "quadrilogy", "anthology", "series", "box set", "bundle", "franchise", "films"]
                            is_collection = any(kw in clean_q.lower() for kw in collection_keywords)

                            if is_collection:
                                unpacked = fetch_collection_movies(clean_q)
                                if unpacked:
                                    st.session_state[f"edit_unpacked_{idx}"] = unpacked
                                    st.session_state.pop(f"edit_matches_{idx}", None)
                                    st.success(f"Found collection with {len(unpacked)} movies!")
                                else:
                                    is_collection = False

                            if not is_collection:
                                st.session_state.pop(f"edit_unpacked_{idx}", None)
                                encoded_q = urllib.parse.quote_plus(clean_q)
                                url_s = f"http://www.omdbapi.com/?s={encoded_q}&apikey={OMDB_API_KEY}"
                                res_s = requests.get(url_s, timeout=4).json()
                                matches = []
                                if res_s.get("Response") == "True":
                                    matches = res_s.get("Search", [])
                                else:
                                    url_t = f"http://www.omdbapi.com/?t={encoded_q}&apikey={OMDB_API_KEY}"
                                    res_t = requests.get(url_t, timeout=4).json()
                                    if res_t.get("Response") == "True":
                                        matches = [{"Title": res_t.get("Title"), "Year": res_t.get("Year"), "Type": res_t.get("Type", "movie"), "imdbID": res_t.get("imdbID")}]

                                if matches:
                                    st.session_state[f"edit_matches_{idx}"] = matches
                                    st.success(f"Found {len(matches)} match(es)!")
                                else:
                                    st.error(f"No results found for '{edit_search_q}'.")
                        except Exception as e:
                            st.error(f"Error fetching metadata: {e}")

                if st.session_state.get(f"edit_unpacked_{idx}"):
                    unpacked_list = st.session_state[f"edit_unpacked_{idx}"]
                    st.info("📦 This item appears to be a collection. Would you like to split it into separate entries?")
                    if st.button("🚀 Split Collection & Replace Current Entry with All Films", key=f"btn_split_exec_{file_path}_{idx}"):
                        save_edited_row(file_path, item_id, {"_DELETE_": True}, title_col)
                        save_multiple_movies_to_csv(file_path, unpacked_list)
                        st.session_state[f"expand_edit_{file_path}_{idx}"] = False
                        st.session_state.pop(f"edit_unpacked_{idx}", None)
                        st.rerun()

                if st.session_state.get(f"edit_matches_{idx}"):
                    matches = st.session_state[f"edit_matches_{idx}"]
                    match_opts = {f"{m['Title']} ({m.get('Year', 'N/A')}) [{m.get('Type', '').capitalize()}]": m["imdbID"] for m in matches}
                    selected_match_label = st.selectbox("Select from found results:", list(match_opts.keys()), key=f"select_edit_match_{file_path}_{idx}")
                    selected_imdb_id = match_opts[selected_match_label]

                    if selected_imdb_id:
                        d_url = f"http://www.omdbapi.com/?i={selected_imdb_id}&apikey={OMDB_API_KEY}"
                        d_res = requests.get(d_url, timeout=4).json()

                        if d_res.get("Response") == "True":
                            col_p1, col_p2 = st.columns([1, 3])
                            with col_p1:
                                p_poster = d_res.get("Poster", "")
                                if p_poster and p_poster != "N/A":
                                    st.image(p_poster, width=70)
                                else:
                                    st.caption("No Poster")
                            with col_p2:
                                st.caption(f"**{d_res.get('Title')}** ({d_res.get('Year')}) | Rated: {d_res.get('Rated')} | Genre: {d_res.get('Genre')}")

                            if st.button("✅ Apply Changes to Form", key=f"btn_apply_edit_{file_path}_{idx}"):
                                st.session_state[f"edit_{file_path}_{idx}_Title"] = d_res.get("Title", "")
                                st.session_state[f"edit_{file_path}_{idx}_Rating"] = d_res.get("Rated", "")
                                st.session_state[f"edit_{file_path}_{idx}_Year Released"] = d_res.get("Year", "")
                                st.session_state[f"edit_{file_path}_{idx}_Length of Movie"] = d_res.get("Runtime", "")
                                st.session_state[f"edit_{file_path}_{idx}_Type"] = d_res.get("Type", "movie").capitalize()
                                st.session_state[f"edit_{file_path}_{idx}_Genre"] = d_res.get("Genre", "")
                                st.session_state[f"edit_{file_path}_{idx}_Image_Path"] = p_poster if p_poster != "N/A" else ""
                                st.success("Loaded selected metadata into fields below!")
                                st.rerun()

                st.markdown("---")

            # --- 🎲 GAME METADATA SEARCH (BGG API) ---
            elif category_type == "game":
                st.markdown("##### 🔍 Search BoardGameGeek Box Art & Metadata")
                col_g1, col_g2 = st.columns([3, 1])
                with col_g1:
                    g_query = st.text_input("Game Search Query", value=item_id, key=f"edit_bgg_q_{file_path}_{idx}")
                with col_g2:
                    st.write("")
                    st.write("")
                    if st.button("Fetch Game Info", key=f"btn_edit_bgg_{file_path}_{idx}"):
                        bgg_matches = fetch_bgg_game_matches(g_query)
                        if bgg_matches:
                            st.session_state[f"edit_bgg_matches_{idx}"] = bgg_matches
                            st.success(f"Found {len(bgg_matches)} BGG match(es)!")
                        else:
                            st.error(f"No BGG entries found for '{g_query}'.")

                if st.session_state.get(f"edit_bgg_matches_{idx}"):
                    bgg_list = st.session_state[f"edit_bgg_matches_{idx}"]
                    bgg_opts = {f"{g['name']} ({g['year']})": g["id"] for g in bgg_list}
                    sel_g_label = st.selectbox("Choose matching game:", list(bgg_opts.keys()), key=f"sel_bgg_{file_path}_{idx}")
                    sel_bgg_id = bgg_opts[sel_g_label]

                    if sel_bgg_id and st.button("✅ Auto-Fill Box Art & Specs", key=f"btn_apply_bgg_{file_path}_{idx}"):
                        g_details = fetch_bgg_game_details(sel_bgg_id)
                        st.session_state[f"edit_{file_path}_{idx}_Title"] = sel_g_label.split(" (")[0]
                        st.session_state[f"edit_{file_path}_{idx}_Number of Players"] = g_details.get("Number of Players", "")
                        st.session_state[f"edit_{file_path}_{idx}_Length of Play"] = g_details.get("Length of Play", "")
                        st.session_state[f"edit_{file_path}_{idx}_Age Rating"] = g_details.get("Age Rating", "")
                        st.session_state[f"edit_{file_path}_{idx}_Image_Path"] = g_details.get("Image_Path", "")
                        st.success("Loaded BGG box art and game specs below!")
                        st.rerun()

                st.markdown("---")

            # --- 🍳 KITCHEN ITEM PHOTO SEARCH ---
            elif category_type == "kitchen":
                st.markdown("##### 📷 Quick Product Photo Search")
                col_k1, col_k2 = st.columns([3, 1])
                with col_k1:
                    k_query = st.text_input("Product Search Query", value=item_id, key=f"edit_kitchen_q_{file_path}_{idx}")
                with col_k2:
                    st.write("")
                    st.write("")
                    if st.button("Suggest Photo", key=f"btn_edit_kitchen_{file_path}_{idx}"):
                        encoded_k = urllib.parse.quote_plus(k_query)
                        suggested_img = f"https://source.unsplash.com/400x300/?{encoded_k},kitchen"
                        st.session_state[f"edit_{file_path}_{idx}_Image_Path"] = suggested_img
                        st.success(f"Generated web photo URL for '{k_query}'!")
                        st.rerun()

                st.markdown("---")

            edit_inputs = {}
            for col_name in editable_cols:
                input_key = f"edit_{file_path}_{idx}_{col_name}"
                if input_key not in st.session_state:
                    st.session_state[input_key] = str(row.get(col_name, ""))

                edit_inputs[col_name] = st.text_input(f"{col_name}", key=input_key)

            col_btn1, col_btn2 = st.columns([1, 1])
            with col_btn1:
                if st.button("💾 Save Changes", key=f"save_{file_path}_{idx}"):
                    if save_edited_row(file_path, item_id, edit_inputs, title_col):
                        st.session_state[f"expand_edit_{file_path}_{idx}"] = False
                        for col_name in editable_cols:
                            st.session_state.pop(f"edit_{file_path}_{idx}_{col_name}", None)
                        st.session_state.pop(f"edit_matches_{idx}", None)
                        st.session_state.pop(f"edit_unpacked_{idx}", None)
                        st.session_state.pop(f"edit_bgg_matches_{idx}", None)
                        st.rerun()

            with col_btn2:
                if st.button("🗑️ Delete Item", key=f"del_{file_path}_{idx}"):
                    if save_edited_row(file_path, item_id, {"_DELETE_": True}, title_col):
                        st.session_state[f"expand_edit_{file_path}_{idx}"] = False
                        for col_name in editable_cols:
                            st.session_state.pop(f"edit_{file_path}_{idx}_{col_name}", None)
                        st.session_state.pop(f"edit_matches_{idx}", None)
                        st.session_state.pop(f"edit_unpacked_{idx}", None)
                        st.session_state.pop(f"edit_bgg_matches_{idx}", None)
                        st.rerun()

        # Render Active Category View
        if active_category == "Movies & TV":
            st.subheader("Movies & TV Collection")
            display_inventory_items(
                df_movies,
                "Title",
                lambda r: f"**Type:** {r.get('Type', '')} | **Rating:** {r.get('Rating', '')}\n\n**Year:** {r.get('Year Released', '')} | **Genre:** {r.get('Genre', '')}",
                lambda r: f"Type: {r.get('Type', '')} | Rating: {r.get('Rating', '')} | Year: {r.get('Year Released', '')} | Genre: {r.get('Genre', '')}",
                "movies_and_tv_collection.csv",
                ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre", "Image_Path"],
                category_type="movie",
            )

        elif active_category == "Board & Card Games":
            st.subheader("Board & Card Games Collection")
            
            # --- BOARD GAME BULK SCAN TOOL ---
            with st.expander("🛠️ Bulk Audit & Auto-Fill Missing Game Box Art"):
                missing_games_mask = (
                    df_games["Image_Path"].isna()
                    | (df_games["Image_Path"].astype(str).str.strip() == "")
                )
                missing_games_df = df_games[missing_games_mask]

                if missing_games_df.empty:
                    st.success("🎉 All titles in your Board & Card Games database have box art!")
                else:
                    st.warning(f"Found {len(missing_games_df)} game(s) missing box art.")
                    if st.button("🔍 Scan BGG for Missing Box Art"):
                        game_scan_results = []
                        progress_bar = st.progress(0)

                        for i, (_, g_row) in enumerate(missing_games_df.iterrows()):
                            g_title = g_row["Title"]
                            matches = fetch_bgg_game_matches(g_title)
                            if matches:
                                details = fetch_bgg_game_details(matches[0]["id"])
                                if details.get("Image_Path"):
                                    game_scan_results.append({
                                        "Title": g_title,
                                        "Found_Image": details["Image_Path"],
                                        "Found_Players": details.get("Number of Players", ""),
                                        "Found_Length": details.get("Length of Play", ""),
                                        "Found_Age": details.get("Age Rating", ""),
                                    })
                            progress_bar.progress((i + 1) / len(missing_games_df))

                        st.session_state["bulk_game_scan_results"] = game_scan_results

                    if st.session_state.get("bulk_game_scan_results"):
                        st.markdown("#### Review Found Game Box Art")
                        if st.button("⚡ Accept All Game Updates"):
                            updates = st.session_state["bulk_game_scan_results"]
                            g_df = safe_load_csv("board_and_card_games_collection.csv", ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"])
                            for item in updates:
                                mask = g_df["Title"].astype(str).str.lower().str.strip() == str(item["Title"]).lower().strip()
                                if mask.any():
                                    idx = g_df[mask].index[0]
                                    if item["Found_Image"]:
                                        g_df.at[idx, "Image_Path"] = item["Found_Image"]
                                    if item["Found_Players"]:
                                        g_df.at[idx, "Number of Players"] = item["Found_Players"]
                            g_df.to_csv("board_and_card_games_collection.csv", index=False)
                            push_csv_to_github("board_and_card_games_collection.csv", "Bulk game metadata update")
                            st.session_state.pop("bulk_game_scan_results", None)
                            st.success("Updated game box art!")
                            st.rerun()

            st.markdown("---")
            display_inventory_items(
                df_games,
                "Title",
                lambda r: f"**Players:** {r.get('Number of Players', '')}\n\n**Length:** {r.get('Length of Play', '')} | **Age:** {r.get('Age Rating', '')}",
                lambda r: f"Players: {r.get('Number of Players', '')} | Length: {r.get('Length of Play', '')} | Age: {r.get('Age Rating', '')} | Style: {r.get('Style of Game', '')}",
                "board_and_card_games_collection.csv",
                ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game", "Image_Path"],
                category_type="game",
            )

        elif active_category == "Kitchen Gear":
            st.subheader("Kitchen Gear Inventory")
            display_inventory_items(
                df_kitchen,
                "Name of Item",
                lambda r: f"**Type:** {r.get('Type of Equipment', '')}\n\n" + (f"[📄 Manual Link]({r['Instruction Manual Link']})" if pd.notna(r.get("Instruction Manual Link")) and str(r.get("Instruction Manual Link")).startswith("http") else ""),
                lambda r: f"Type: {r.get('Type of Equipment', '')} " + (f"| [📄 Manual Link]({r['Instruction Manual Link']})" if pd.notna(r.get("Instruction Manual Link")) and str(r.get("Instruction Manual Link")).startswith("http") else ""),
                "kitchen_gear_inventory_v2.csv",
                ["Name of Item", "Type of Equipment", "Instruction Manual Link", "Image_Path"],
                category_type="kitchen",
            )
