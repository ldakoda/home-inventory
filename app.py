import os
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


def update_movie_in_csv(file_path, title, new_data_dict):
    """Updates missing metadata for a specific title directly in the CSV."""
    if not os.path.exists(file_path):
        return False

    df = safe_load_csv(
        file_path,
        [
            "Title",
            "Rating",
            "Year Released",
            "Length of Movie",
            "Type",
            "Genre",
            "Image_Path",
        ],
    )

    mask = (
        df["Title"]
        .astype(str)
        .str.lower()
        .str.strip()
        == str(title).lower().strip()
    )
    if not mask.any():
        return False

    df = df.astype(object)
    idx = df[mask].index[0]
    for key, val in new_data_dict.items():
        if val and str(val).strip() != "":
            df.at[idx, key] = str(val)

    df.to_csv(file_path, index=False)
    push_csv_to_github(file_path, f"Update metadata for {title}")
    return True


def bulk_update_movies_in_csv(file_path, updates_list):
    """Batch updates multiple movie entries at once in the CSV."""
    if not os.path.exists(file_path):
        return False

    df = safe_load_csv(
        file_path,
        [
            "Title",
            "Rating",
            "Year Released",
            "Length of Movie",
            "Type",
            "Genre",
            "Image_Path",
        ],
    )
    df = df.astype(object)

    for item in updates_list:
        mask = (
            df["Title"]
            .astype(str)
            .str.lower()
            .str.strip()
            == str(item["Title"]).lower().strip()
        )
        if mask.any():
            idx = df[mask].index[0]
            update_dict = {
                "Year Released": item["Found_Year"],
                "Rating": item["Found_Rating"],
                "Length of Movie": item["Found_Length"],
                "Genre": item["Found_Genre"],
                "Image_Path": item["Found_Poster"],
            }
            for k, v in update_dict.items():
                if v and str(v).strip() != "":
                    df.at[idx, k] = str(v)

    df.to_csv(file_path, index=False)
    push_csv_to_github(file_path, "Bulk audit metadata update")
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
            st.subheader("Movie / TV Show Entry")

            if not OMDB_API_KEY:
                st.warning(
                    "⚠️ `OMDB_KEY` environment secret is not set. You can manually enter movie details below."
                )

            st.markdown("#### 1. Search Movie Database")
            col_search1, col_search2 = st.columns([3, 1])
            with col_search1:
                search_title = st.text_input(
                    "Search Title",
                    placeholder="e.g., Avatar, Batman, Star Wars",
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
                        try:
                            url = f"http://www.omdbapi.com/?s={search_title}&apikey={OMDB_API_KEY}"
                            res = requests.get(url, timeout=5).json()

                            if res.get("Response") == "True":
                                st.session_state["search_results"] = res.get(
                                    "Search", []
                                )
                                st.success(
                                    f"Found {len(st.session_state['search_results'])} match(es)!"
                                )
                            else:
                                st.session_state["search_results"] = []
                                st.error(
                                    f"No results found for '{search_title}'."
                                )
                        except Exception as e:
                            st.error(f"Error fetching search results: {e}")

            if st.session_state.get("search_results"):
                st.markdown("---")
                st.markdown("#### 2. Select the Correct Match")

                options = {
                    f"{m['Title']} ({m.get('Year', 'N/A')}) [{m.get('Type', '').capitalize()}]": m[
                        "imdbID"
                    ]
                    for m in st.session_state["search_results"]
                }

                selected_label = st.selectbox(
                    "Choose from search results:", list(options.keys())
                )
                selected_imdb_id = options[selected_label]

                if selected_imdb_id:
                    detail_url = f"http://www.omdbapi.com/?i={selected_imdb_id}&apikey={OMDB_API_KEY}"
                    full_res = requests.get(detail_url, timeout=5).json()

                    if full_res.get("Response") == "True":
                        col_preview1, col_preview2 = st.columns([1, 3])

                        with col_preview1:
                            poster = full_res.get("Poster", "")
                            if poster and poster != "N/A":
                                st.image(
                                    poster,
                                    caption="Movie Poster",
                                    use_container_width=True,
                                )
                            else:
                                st.caption("📷 No Poster Available")

                        with col_preview2:
                            st.subheader(
                                f"{full_res.get('Title')} ({full_res.get('Year')})"
                            )
                            st.markdown(
                                f"**Type:** {full_res.get('Type', '').capitalize()} | **Rated:** {full_res.get('Rated')}"
                            )
                            st.markdown(
                                f"**Runtime:** {full_res.get('Runtime')} | **Genre:** {full_res.get('Genre')}"
                            )
                            st.write(
                                f"**Plot:** {full_res.get('Plot', 'N/A')}"
                            )

                            if st.button("✅ Accept & Use This Movie"):
                                st.session_state["m_title"] = full_res.get(
                                    "Title", ""
                                )
                                st.session_state["m_year"] = full_res.get(
                                    "Year", ""
                                )
                                st.session_state["m_rating"] = full_res.get(
                                    "Rated", ""
                                )
                                st.session_state["m_length"] = full_res.get(
                                    "Runtime", ""
                                )
                                st.session_state["m_type"] = full_res.get(
                                    "Type", "movie"
                                ).capitalize()
                                st.session_state["m_genre"] = full_res.get(
                                    "Genre", ""
                                )
                                st.session_state["m_poster"] = (
                                    poster if poster != "N/A" else ""
                                )
                                st.success(
                                    f"Loaded '{full_res.get('Title')}' into form below!"
                                )

            st.markdown("---")
            st.markdown("#### 3. Verify & Save Entry")

            with st.form("movie_form", clear_on_submit=True):
                title = st.text_input(
                    "Title *", value=st.session_state.get("m_title", "")
                )
                rating = st.text_input(
                    "Rating (PG, PG-13, R)",
                    value=st.session_state.get("m_rating", ""),
                )
                year = st.text_input(
                    "Year Released", value=st.session_state.get("m_year", "")
                )
                length = st.text_input(
                    "Length of Movie",
                    value=st.session_state.get("m_length", ""),
                )
                m_type = st.selectbox(
                    "Type",
                    ["Movie", "TV"],
                    index=(
                        0
                        if st.session_state.get("m_type", "Movie") == "Movie"
                        else 1
                    ),
                )
                genre = st.text_input(
                    "Genre", value=st.session_state.get("m_genre", "")
                )
                poster_link = st.text_input(
                    "Poster / Image URL",
                    value=st.session_state.get("m_poster", ""),
                )

                uploaded_image = st.file_uploader(
                    "Or Upload Custom Image File", type=["jpg", "png", "jpeg"]
                )

                if st.form_submit_button("Save Movie to Inventory"):
                    if not title:
                        st.error("Title is required.")
                    else:
                        image_path = poster_link
                        if uploaded_image:
                            image_path = os.path.join(
                                IMAGE_DIR, uploaded_image.name
                            )
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
                        existing_df = safe_load_csv(
                            file_path,
                            [
                                "Title",
                                "Rating",
                                "Year Released",
                                "Length of Movie",
                                "Type",
                                "Genre",
                                "Image_Path",
                            ],
                        )
                        updated_df = pd.concat(
                            [existing_df, pd.DataFrame([new_entry])],
                            ignore_index=True,
                        )
                        updated_df.to_csv(file_path, index=False)
                        push_csv_to_github(file_path, f"Add movie '{title}'")

                        st.success(
                            f"Added '{title}' to Movies & TV database!"
                        )

                        for key in [
                            "m_title",
                            "m_year",
                            "m_rating",
                            "m_length",
                            "m_type",
                            "m_genre",
                            "m_poster",
                            "search_results",
                        ]:
                            st.session_state.pop(key, None)

        # --- CATEGORY 2: BOARD & CARD GAMES ---
        elif category == "Board & Card Games":
            st.subheader("Board & Card Game Entry")
            with st.form("game_form", clear_on_submit=True):
                title = st.text_input("Game Title *")
                players = st.text_input(
                    "Number of Players (e.g., 2-4 Players)"
                )
                length = st.text_input("Length of Play (e.g., 30-45 min)")
                age = st.text_input("Age Rating (e.g., 10+)")
                style = st.text_input("Style of Game (Board, Card, Dice)")
                uploaded_image = st.file_uploader(
                    "Upload Box Photo", type=["jpg", "png", "jpeg"]
                )

                if st.form_submit_button("Save Game to Inventory"):
                    if not title:
                        st.error("Game title is required.")
                    else:
                        image_path = ""
                        if uploaded_image:
                            image_path = os.path.join(
                                IMAGE_DIR, uploaded_image.name
                            )
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
                        existing_df = safe_load_csv(
                            file_path,
                            [
                                "Title",
                                "Number of Players",
                                "Length of Play",
                                "Age Rating",
                                "Style of Game",
                                "Image_Path",
                            ],
                        )
                        updated_df = pd.concat(
                            [existing_df, pd.DataFrame([new_entry])],
                            ignore_index=True,
                        )
                        updated_df.to_csv(file_path, index=False)
                        push_csv_to_github(file_path, f"Add game '{title}'")
                        st.success(f"Added '{title}' to Games database!")

        # --- CATEGORY 3: KITCHEN GEAR ---
        elif category == "Kitchen Gear":
            st.subheader("Kitchen Gear Entry")
            with st.form("kitchen_form", clear_on_submit=True):
                title = st.text_input("Name of Item *")
                eq_type = st.selectbox(
                    "Type of Equipment",
                    ["Appliance", "Cookware", "Appliance Accessory", "Utensil"],
                )
                manual = st.text_input("Instruction Manual Link (URL)")
                uploaded_image = st.file_uploader(
                    "Upload Item Photo", type=["jpg", "png", "jpeg"]
                )

                if st.form_submit_button("Save Kitchen Gear"):
                    if not title:
                        st.error("Item name is required.")
                    else:
                        image_path = ""
                        if uploaded_image:
                            image_path = os.path.join(
                                IMAGE_DIR, uploaded_image.name
                            )
                            with open(image_path, "wb") as f:
                                f.write(uploaded_image.getbuffer())

                        new_entry = {
                            "Name of Item": title,
                            "Type of Equipment": eq_type,
                            "Instruction Manual Link": manual,
                            "Image_Path": image_path,
                        }
                        file_path = "kitchen_gear_inventory_v2.csv"
                        existing_df = safe_load_csv(
                            file_path,
                            [
                                "Name of Item",
                                "Type of Equipment",
                                "Instruction Manual Link",
                                "Image_Path",
                            ],
                        )
                        updated_df = pd.concat(
                            [existing_df, pd.DataFrame([new_entry])],
                            ignore_index=True,
                        )
                        updated_df.to_csv(file_path, index=False)
                        push_csv_to_github(file_path, f"Add kitchen item '{title}'")
                        st.success(
                            f"Added '{title}' to Kitchen Gear database!"
                        )

    # -----------------------------------------------------------------------------
    # 7. PAGE: BROWSE INVENTORY WITH TOP CONTROL BAR
    # -----------------------------------------------------------------------------
    elif app_mode == "🔍 Browse Inventory":
        st.title("🍊 Browse Home Inventory")

        df_movies = safe_load_csv(
            "movies_and_tv_collection.csv",
            [
                "Title",
                "Rating",
                "Year Released",
                "Length of Movie",
                "Type",
                "Genre",
                "Image_Path",
            ],
        )
        df_games = safe_load_csv(
            "board_and_card_games_collection.csv",
            [
                "Title",
                "Number of Players",
                "Length of Play",
                "Age Rating",
                "Style of Game",
                "Image_Path",
            ],
        )
        df_kitchen = safe_load_csv(
            "kitchen_gear_inventory_v2.csv",
            [
                "Name of Item",
                "Type of Equipment",
                "Instruction Manual Link",
                "Image_Path",
            ],
        )

        # --- UNIFIED CONTROL BAR (ALWAYS VISIBLE AT TOP) ---
        c_search, c_sort, c_order, c_view = st.columns([3, 2, 1.5, 2])

        with c_search:
            global_search_q = st.text_input("🔍 Search items across categories...")

        with c_sort:
            sort_by_col = st.selectbox(
                "Sort By:",
                ["Title", "Year Released", "Rating", "Genre", "Number of Players", "Name of Item", "Type of Equipment"],
            )

        with c_order:
            order_by = st.radio("Order:", ["Asc", "Desc"], horizontal=True)

        with c_view:
            layout_view = st.radio("Layout View:", ["🎴 Cards", "📋 List"], horizontal=True)

        st.markdown("---")

        tab_movies, tab_games, tab_kitchen = st.tabs(
            ["Movies & TV", "Board & Card Games", "Kitchen Gear"]
        )

        def display_inventory_items(
            df,
            title_col,
            details_func,
            summary_inline_func,
            file_path,
            editable_cols,
            is_movie_tab=False,
            image_col="Image_Path",
        ):
            if df.empty:
                st.info("No items in this category yet.")
                return

            # Apply Search Filter
            if global_search_q:
                mask = df[title_col].astype(str).str.contains(global_search_q, case=False)
                df = df[mask]

            if df.empty:
                st.info("No items matching your search.")
                return

            # Apply Sorting Filter
            is_asc = order_by == "Asc"
            if sort_by_col in df.columns:
                df = df.sort_values(
                    by=sort_by_col,
                    ascending=is_asc,
                    key=lambda x: x.astype(str).str.lower(),
                )

            # --- RENDER CARDS VIEW ---
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
                                render_edit_form(
                                    idx, item_id, row, editable_cols, file_path, title_col, is_movie_tab
                                )

            # --- RENDER LIST VIEW ---
            else:
                for idx, row in df.reset_index(drop=True).iterrows():
                    item_id = str(row[title_col])
                    with st.container(border=True):
                        col_text, col_img = st.columns([5, 1])

                        with col_text:
                            st.markdown(f"### {item_id}")
                            st.markdown(summary_inline_func(row))

                        with col_img:
                            img_val = row.get(image_col, "")
                            if pd.notna(img_val) and str(img_val).strip() != "":
                                st.image(str(img_val), width=45)
                            else:
                                st.caption("📷")

                        with st.expander(f"✏️ Edit / Delete '{item_id}'"):
                            render_edit_form(
                                idx, item_id, row, editable_cols, file_path, title_col, is_movie_tab
                            )

        def render_edit_form(idx, item_id, row, editable_cols, file_path, title_col, is_movie_tab):
            """Form renderer for editing row attributes with metadata search."""
            if is_movie_tab and OMDB_API_KEY:
                st.markdown("##### 🔍 Search & Auto-Fill Metadata")
                col_m1, col_m2 = st.columns([3, 1])
                with col_m1:
                    edit_search_q = st.text_input(
                        "Search Title",
                        value=item_id,
                        key=f"edit_search_q_{file_path}_{idx}",
                    )
                with col_m2:
                    st.write("")
                    st.write("")
                    if st.button("Search & Fill", key=f"btn_edit_search_{file_path}_{idx}"):
                        try:
                            url = f"http://www.omdbapi.com/?s={edit_search_q}&apikey={OMDB_API_KEY}"
                            res = requests.get(url, timeout=4).json()
                            if res.get("Response") == "True":
                                st.session_state[f"edit_matches_{idx}"] = res.get("Search", [])
                                st.success(f"Found {len(res.get('Search', []))} match(es)!")
                            else:
                                st.error("No matches found.")
                        except Exception as e:
                            st.error(f"Error: {e}")

                if st.session_state.get(f"edit_matches_{idx}"):
                    matches = st.session_state[f"edit_matches_{idx}"]
                    opts = {
                        f"{m['Title']} ({m.get('Year', 'N/A')}) [{m.get('Type', '').capitalize()}]": m["imdbID"]
                        for m in matches
                    }
                    selected_imdb_label = st.selectbox(
                        "Select correct match:",
                        list(opts.keys()),
                        key=f"select_edit_match_{file_path}_{idx}",
                    )
                    if st.button("✅ Load Into Form", key=f"btn_load_edit_{file_path}_{idx}"):
                        selected_imdb_id = opts[selected_imdb_label]
                        d_url = f"http://www.omdbapi.com/?i={selected_imdb_id}&apikey={OMDB_API_KEY}"
                        d_res = requests.get(d_url, timeout=4).json()
                        if d_res.get("Response") == "True":
                            st.session_state[f"edit_{file_path}_{idx}_Title"] = d_res.get("Title", "")
                            st.session_state[f"edit_{file_path}_{idx}_Rating"] = d_res.get("Rated", "")
                            st.session_state[f"edit_{file_path}_{idx}_Year Released"] = d_res.get("Year", "")
                            st.session_state[f"edit_{file_path}_{idx}_Length of Movie"] = d_res.get("Runtime", "")
                            st.session_state[f"edit_{file_path}_{idx}_Type"] = d_res.get("Type", "movie").capitalize()
                            st.session_state[f"edit_{file_path}_{idx}_Genre"] = d_res.get("Genre", "")
                            p_url = d_res.get("Poster", "")
                            st.session_state[f"edit_{file_path}_{idx}_Image_Path"] = p_url if p_url != "N/A" else ""
                            st.success("Loaded metadata into fields below! Click 'Save Changes' to update.")
                            st.rerun()

                st.markdown("---")

            edit_inputs = {}
            for col_name in editable_cols:
                input_key = f"edit_{file_path}_{idx}_{col_name}"
                if input_key not in st.session_state:
                    st.session_state[input_key] = str(row.get(col_name, ""))

                edit_inputs[col_name] = st.text_input(
                    f"{col_name}",
                    key=input_key,
                )

            col_btn1, col_btn2 = st.columns([1, 1])
            with col_btn1:
                if st.button("💾 Save Changes", key=f"save_{file_path}_{idx}"):
                    if save_edited_row(file_path, item_id, edit_inputs, title_col):
                        st.success(f"Saved changes to '{item_id}'!")
                        st.rerun()

            with col_btn2:
                if st.button("🗑️ Delete Item", key=f"del_{file_path}_{idx}"):
                    if save_edited_row(file_path, item_id, {"_DELETE_": True}, title_col):
                        st.warning(f"Deleted '{item_id}'.")
                        st.rerun()

        with tab_movies:
            # Bulk Audit Tool
            with st.expander("🛠️ Bulk Audit & Auto-Fill Missing Metadata"):
                if not OMDB_API_KEY:
                    st.error(
                        "Missing `OMDB_KEY` in secrets. Please configure it to use auto-fill."
                    )
                else:
                    missing_mask = (
                        df_movies["Year Released"].isna()
                        | (
                            df_movies["Year Released"]
                            .astype(str)
                            .str.strip()
                            == ""
                        )
                        | df_movies["Rating"].isna()
                        | (df_movies["Rating"].astype(str).str.strip() == "")
                        | df_movies["Image_Path"].isna()
                        | (
                            df_movies["Image_Path"]
                            .astype(str)
                            .str.strip()
                            == ""
                        )
                    )
                    missing_df = df_movies[missing_mask]

                    if missing_df.empty:
                        st.success(
                            "🎉 All titles in your Movies & TV database have complete metadata!"
                        )
                    else:
                        st.warning(
                            f"Found {len(missing_df)} item(s) missing metadata or posters."
                        )

                        if st.button("🔍 Scan Database for Missing Data"):
                            scan_results = []
                            progress_bar = st.progress(0)

                            for i, (_, m_row) in enumerate(
                                missing_df.iterrows()
                            ):
                                m_title = m_row["Title"]
                                try:
                                    url = f"http://www.omdbapi.com/?t={m_title}&apikey={OMDB_API_KEY}"
                                    res = requests.get(url, timeout=4).json()
                                    if res.get("Response") == "True":
                                        scan_results.append(
                                            {
                                                "Title": m_title,
                                                "Found_Year": res.get(
                                                    "Year", ""
                                                ),
                                                "Found_Rating": res.get(
                                                    "Rated", ""
                                                ),
                                                "Found_Length": res.get(
                                                    "Runtime", ""
                                                ),
                                                "Found_Genre": res.get(
                                                    "Genre", ""
                                                ),
                                                "Found_Poster": (
                                                    res.get("Poster", "")
                                                    if res.get("Poster")
                                                    != "N/A"
                                                    else ""
                                                ),
                                            }
                                        )
                                except Exception:
                                    pass
                                progress_bar.progress(
                                    (i + 1) / len(missing_df)
                                )

                            st.session_state["bulk_scan_results"] = scan_results

                        if st.session_state.get("bulk_scan_results"):
                            st.markdown("#### Review Found Metadata")

                            if st.button("⚡ Accept All Changes"):
                                if bulk_update_movies_in_csv(
                                    "movies_and_tv_collection.csv",
                                    st.session_state["bulk_scan_results"],
                                ):
                                    st.session_state["bulk_scan_results"] = []
                                    st.success(
                                        "Updated and synced all missing metadata to GitHub!"
                                    )
                                    st.rerun()

                            st.markdown("---")
                            for res_item in list(
                                st.session_state["bulk_scan_results"]
                            ):
                                with st.container(border=True):
                                    col_a, col_b, col_c = st.columns([1, 3, 1])
                                    with col_a:
                                        if res_item["Found_Poster"]:
                                            st.image(
                                                res_item["Found_Poster"],
                                                width=80,
                                            )
                                        else:
                                            st.caption("No poster")
                                    with col_b:
                                        st.write(f"**{res_item['Title']}**")
                                        st.caption(
                                            f"Year: {res_item['Found_Year']} | Rating: {res_item['Found_Rating']} | "
                                            f"Runtime: {res_item['Found_Length']} | Genre: {res_item['Found_Genre']}"
                                        )
                                    with col_c:
                                        if st.button(
                                            "✅ Accept & Update",
                                            key=f"accept_{res_item['Title']}",
                                        ):
                                            update_dict = {
                                                "Year Released": res_item[
                                                    "Found_Year"
                                                ],
                                                "Rating": res_item[
                                                    "Found_Rating"
                                                ],
                                                "Length of Movie": res_item[
                                                    "Found_Length"
                                                ],
                                                "Genre": res_item[
                                                    "Found_Genre"
                                                ],
                                                "Image_Path": res_item[
                                                    "Found_Poster"
                                                ],
                                            }
                                            if update_movie_in_csv(
                                                "movies_and_tv_collection.csv",
                                                res_item["Title"],
                                                update_dict,
                                            ):
                                                st.session_state[
                                                    "bulk_scan_results"
                                                ] = [
                                                    item
                                                    for item in st.session_state[
                                                        "bulk_scan_results"
                                                    ]
                                                    if item["Title"]
                                                    != res_item["Title"]
                                                ]
                                                st.success(
                                                    f"Updated '{res_item['Title']}'!"
                                                )
                                                st.rerun()

            st.markdown("---")
            display_inventory_items(
                df_movies,
                "Title",
                lambda r: f"**Type:** {r.get('Type', '')} | **Rating:** {r.get('Rating', '')}\n\n"
                f"**Year:** {r.get('Year Released', '')} | **Genre:** {r.get('Genre', '')}",
                lambda r: f"**Type:** {r.get('Type', '')} | **Rating:** {r.get('Rating', '')} | **Year:** {r.get('Year Released', '')} | **Genre:** {r.get('Genre', '')}",
                "movies_and_tv_collection.csv",
                [
                    "Title",
                    "Rating",
                    "Year Released",
                    "Length of Movie",
                    "Type",
                    "Genre",
                    "Image_Path",
                ],
                is_movie_tab=True,
            )

        with tab_games:
            display_inventory_items(
                df_games,
                "Title",
                lambda r: f"**Players:** {r.get('Number of Players', '')}\n\n"
                f"**Length:** {r.get('Length of Play', '')} | **Age:** {r.get('Age Rating', '')}",
                lambda r: f"**Players:** {r.get('Number of Players', '')} | **Length:** {r.get('Length of Play', '')} | **Age:** {r.get('Age Rating', '')} | **Style:** {r.get('Style of Game', '')}",
                "board_and_card_games_collection.csv",
                [
                    "Title",
                    "Number of Players",
                    "Length of Play",
                    "Age Rating",
                    "Style of Game",
                    "Image_Path",
                ],
            )

        with tab_kitchen:
            display_inventory_items(
                df_kitchen,
                "Name of Item",
                lambda r: f"**Type:** {r.get('Type of Equipment', '')}\n\n"
                + (
                    f"[📄 Manual Link]({r['Instruction Manual Link']})"
                    if pd.notna(r.get("Instruction Manual Link"))
                    and str(r.get("Instruction Manual Link")).startswith("http")
                    else ""
                ),
                lambda r: f"**Type:** {r.get('Type of Equipment', '')} "
                + (
                    f"| [📄 Manual Link]({r['Instruction Manual Link']})"
                    if pd.notna(r.get("Instruction Manual Link"))
                    and str(r.get("Instruction Manual Link")).startswith("http")
                    else ""
                ),
                "kitchen_gear_inventory_v2.csv",
                [
                    "Name of Item",
                    "Type of Equipment",
                    "Instruction Manual Link",
                    "Image_Path",
                ],
            )
