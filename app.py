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
# 2. AUTHENTICATION
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
    # 3. NAVIGATION & LOGOUT
    # -----------------------------------------------------------------------------
    st.sidebar.title("Navigation")
    app_mode = st.sidebar.radio(
        "Select Page", ["🔍 Browse Inventory", "➕ Add New Item"]
    )

    if st.sidebar.button("Log Out"):
        st.session_state["authenticated"] = False
        st.rerun()

    # -----------------------------------------------------------------------------
    # 4. PAGE: ADD NEW ITEM
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
                    "⚠️ `OMDB_KEY` environment secret is not set. You can still manually enter movie details below."
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
                            # Use OMDb Search Endpoint (?s=)
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

            # Display Search Results Dropdown & Selection Card
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

                # Fetch full metadata for selected IMDb ID
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

                        new_data = pd.DataFrame(
                            [
                                {
                                    "Title": title,
                                    "Rating": rating,
                                    "Year Released": year,
                                    "Length of Movie": length,
                                    "Type": m_type,
                                    "Genre": genre,
                                    "Image_Path": image_path,
                                }
                            ]
                        )
                        file_path = "movies_and_tv_collection.csv"
                        new_data.to_csv(
                            file_path,
                            mode="a",
                            header=not os.path.exists(file_path),
                            index=False,
                        )
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

                        new_data = pd.DataFrame(
                            [
                                {
                                    "Title": title,
                                    "Number of Players": players,
                                    "Length of Play": length,
                                    "Age Rating": age,
                                    "Style of Game": style,
                                    "Image_Path": image_path,
                                }
                            ]
                        )
                        file_path = "board_and_card_games_collection.csv"
                        new_data.to_csv(
                            file_path,
                            mode="a",
                            header=not os.path.exists(file_path),
                            index=False,
                        )
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

                        new_data = pd.DataFrame(
                            [
                                {
                                    "Name of Item": title,
                                    "Type of Equipment": eq_type,
                                    "Instruction Manual Link": manual,
                                    "Image_Path": image_path,
                                }
                            ]
                        )
                        file_path = "kitchen_gear_inventory_v2.csv"
                        new_data.to_csv(
                            file_path,
                            mode="a",
                            header=not os.path.exists(file_path),
                            index=False,
                        )
                        st.success(
                            f"Added '{title}' to Kitchen Gear database!"
                        )

    # -----------------------------------------------------------------------------
    # 5. PAGE: BROWSE INVENTORY
    # -----------------------------------------------------------------------------
    elif app_mode == "🔍 Browse Inventory":
        st.title("🍊 Browse Home Inventory")

        df_movies = (
            pd.read_csv("movies_and_tv_collection.csv")
            if os.path.exists("movies_and_tv_collection.csv")
            else pd.DataFrame()
        )
        df_games = (
            pd.read_csv("board_and_card_games_collection.csv")
            if os.path.exists("board_and_card_games_collection.csv")
            else pd.DataFrame()
        )
        df_kitchen = (
            pd.read_csv("kitchen_gear_inventory_v2.csv")
            if os.path.exists("kitchen_gear_inventory_v2.csv")
            else pd.DataFrame()
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
