import os
import pandas as pd
import requests
import streamlit as st

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

# Fetch OMDb API Key securely from environment / GitHub Secrets
OMDB_API_KEY = os.getenv("OMDB_KEY", "")

# -----------------------------------------------------------------------------
# 2. HELPER FUNCTIONS FOR RESILIENT CSV LOADING & SAVING
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

    # Locate row index matching title
    mask = (
        df["Title"]
        .astype(str)
        .str.lower()
        .str.strip()
        == str(title).lower().strip()
    )
    if not mask.any():
        return False

    # Convert DataFrame columns to object/string to prevent integer dtype conflicts
    df = df.astype(object)

    idx = df[mask].index[0]
    for key, val in new_data_dict.items():
        if val and str(val).strip() != "":
            df.at[idx, key] = str(val)

    df.to_csv(file_path, index=False)
    return True


# -----------------------------------------------------------------------------
# 3. AUTHENTICATION
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
    # 4. NAVIGATION & LOGOUT
    # -----------------------------------------------------------------------------
    st.sidebar.title("Navigation")
    app_mode = st.sidebar.radio(
        "Select Page", ["🔍 Browse Inventory", "➕ Add New Item"]
    )

    if st.sidebar.button("Log Out"):
        st.session_state["authenticated"] = False
        st.rerun()

    # -----------------------------------------------------------------------------
    # 5. PAGE: ADD NEW ITEM
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

            # --- SEARCH & SELECT SECTION ---
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

            # Store search results in session state
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

            # Display Search Results Dropdown & Selection Preview
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

                        st.success(
                            f"Added '{title}' to Movies & TV database!"
                        )

                        # Clear session state
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
                        st.success(
                            f"Added '{title}' to Kitchen Gear database!"
                        )

    # -----------------------------------------------------------------------------
    # 6. PAGE: BROWSE INVENTORY
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

        search_query = st.text_input("🔍 Search items across categories...")

        tab_movies, tab_games, tab_kitchen = st.tabs(
            ["Movies & TV", "Board & Card Games", "Kitchen Gear"]
        )

        def display_cards(
            df, title_col, details_func, image_col="Image_Path"
        ):
            if df.empty:
                st.info("No items in this category yet.")
                return

            if search_query:
                mask = (
                    df[title_col]
                    .astype(str)
                    .str.contains(search_query, case=False)
                )
                df = df[mask]

            if df.empty:
                st.info("No items matching your search.")
                return

            cols = st.columns(3)
            for idx, row in df.reset_index(drop=True).iterrows():
                col = cols[idx % 3]
                with col:
                    with st.container(border=True):
                        img_val = row.get(image_col, "")
                        if pd.notna(img_val) and str(img_val).strip() != "":
                            st.image(str(img_val), use_container_width=True)
                        else:
                            st.caption("📷 No image available")

                        st.subheader(row[title_col])
                        st.write(details_func(row))

        with tab_movies:
            # --- MISSING METADATA AUDIT TOOL ---
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
                            for res_item in st.session_state[
                                "bulk_scan_results"
                            ]:
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
                                                st.success(
                                                    f"Updated '{res_item['Title']}'!"
                                                )
                                                st.rerun()

            st.markdown("---")
            display_cards(
                df_movies,
                "Title",
                lambda r: f"**Type:** {r.get('Type', '')} | **Rating:** {r.get('Rating', '')}\n\n"
                f"**Year:** {r.get('Year Released', '')} | **Genre:** {r.get('Genre', '')}",
            )

        with tab_games:
            display_cards(
                df_games,
                "Title",
                lambda r: f"**Players:** {r.get('Number of Players', '')}\n\n"
                f"**Length:** {r.get('Length of Play', '')} | **Age:** {r.get('Age Rating', '')}",
            )

        with tab_kitchen:
            display_cards(
                df_kitchen,
                "Name of Item",
                lambda r: f"**Type:** {r.get('Type of Equipment', '')}\n\n"
                + (
                    f"[📄 Manual Link]({r['Instruction Manual Link']})"
                    if pd.notna(r.get("Instruction Manual Link"))
                    and str(r.get("Instruction Manual Link")).startswith("http")
                    else ""
                ),
            )
