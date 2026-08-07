import os
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Home Inventory System", page_icon="🏠", layout="wide"
)

# Directory to store uploaded item images
IMAGE_DIR = "uploaded_images"
os.makedirs(IMAGE_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# 1. AUTHENTICATION & METADATA HELPER
# -----------------------------------------------------------------------------
PIN_CODE = "1234"


def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if not st.session_state["authenticated"]:
        st.title("🏠 Home Inventory Access")
        input_pin = st.text_input(
            "Enter Invite Code / PIN:", type="password", key="pin"
        )
        if st.button("Login"):
            if input_pin == PIN_CODE:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Invalid code.")
        return False
    return True


def fetch_movie_omdb(title, api_key="trilogy"):
    """Fetches movie metadata and poster from OMDb API."""
    try:
        url = f"http://www.omdbapi.com/?t={title}&apikey={api_key}"
        res = requests.get(url, timeout=5).json()
        if res.get("Response") == "True":
            return {
                "title": res.get("Title", title),
                "year": res.get("Year", ""),
                "rating": res.get("Rated", "N/A"),
                "length": res.get("Runtime", "N/A"),
                "genre": res.get("Genre", "N/A"),
                "type": res.get("Type", "movie").capitalize(),
                "poster": (
                    res.get("Poster") if res.get("Poster") != "N/A" else None
                ),
            }
    except Exception:
        pass
    return None


if check_password():
    # -----------------------------------------------------------------------------
    # 2. NAVIGATION SIDEBAR
    # -----------------------------------------------------------------------------
    st.sidebar.title("Navigation")
    app_mode = st.sidebar.radio(
        "Select Page", ["🔍 Browse Inventory", "➕ Add New Item"]
    )

    if st.sidebar.button("Log Out"):
        st.session_state["authenticated"] = False
        st.rerun()

    # -----------------------------------------------------------------------------
    # 3. PAGE: ADD NEW ITEM (CATEGORY-SPECIFIC + AUTO-FILL + IMAGE UPLOAD)
    # -----------------------------------------------------------------------------
    if app_mode == "➕ Add New Item":
        st.title("➕ Add New Inventory Item")

        category = st.selectbox(
            "Select Item Category",
            ["Movies & TV", "Board & Card Games", "Kitchen Gear"],
        )

        # ------------------- CATEGORY 1: MOVIES & TV -------------------
        if category == "Movies & TV":
            st.subheader("Movie / TV Show Entry")

            # Metadata Auto-Fill Section
            col_search1, col_search2 = st.columns([3, 1])
            with col_search1:
                search_title = st.text_input(
                    "Search Title to Auto-Fill Metadata",
                    placeholder="e.g., Inception, Avatar",
                )
            with col_search2:
                st.write("")
                st.write("")
                if st.button("🔍 Auto-Fill Details"):
                    if search_title:
                        meta = fetch_movie_omdb(search_title)
                        if meta:
                            st.session_state["m_title"] = meta["title"]
                            st.session_state["m_year"] = meta["year"]
                            st.session_state["m_rating"] = meta["rating"]
                            st.session_state["m_length"] = meta["length"]
                            st.session_state["m_type"] = meta["type"]
                            st.session_state["m_genre"] = meta["genre"]
                            st.session_state["m_poster"] = meta["poster"]
                            st.success(
                                f"Metadata found for '{meta['title']}'!"
                            )
                        else:
                            st.warning("No movie metadata found.")

            with st.form("movie_form", clear_on_submit=True):
                title = st.text_input(
                    "Title", value=st.session_state.get("m_title", "")
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

                uploaded_image = st.file_uploader(
                    "Upload Custom Image/Poster", type=["jpg", "png", "jpeg"]
                )

                if st.form_submit_button("Save Movie to Inventory"):
                    image_path = ""
                    if uploaded_image:
                        image_path = os.path.join(
                            IMAGE_DIR, uploaded_image.name
                        )
                        with open(image_path, "wb") as f:
                            f.write(uploaded_image.getbuffer())
                    elif st.session_state.get("m_poster"):
                        image_path = st.session_state.get("m_poster")

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
                    st.success(f"Added '{title}' to Movies & TV database!")

        # ------------------- CATEGORY 2: BOARD & CARD GAMES -------------------
        elif category == "Board & Card Games":
            st.subheader("Board & Card Game Entry")
            with st.form("game_form", clear_on_submit=True):
                title = st.text_input("Game Title")
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

        # ------------------- CATEGORY 3: KITCHEN GEAR -------------------
        elif category == "Kitchen Gear":
            st.subheader("Kitchen Gear Entry")
            with st.form("kitchen_form", clear_on_submit=True):
                title = st.text_input("Name of Item")
                eq_type = st.selectbox(
                    "Type of Equipment",
                    ["Appliance", "Cookware", "Appliance Accessory", "Utensil"],
                )
                manual = st.text_input("Instruction Manual Link (URL)")
                uploaded_image = st.file_uploader(
                    "Upload Item Photo", type=["jpg", "png", "jpeg"]
                )

                if st.form_submit_button("Save Kitchen Gear"):
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
                    st.success(f"Added '{title}' to Kitchen Gear database!")

    # -----------------------------------------------------------------------------
    # 4. PAGE: BROWSE INVENTORY WITH IMAGES
    # -----------------------------------------------------------------------------
    elif app_mode == "🔍 Browse Inventory":
        st.title("🍊 Browse Home Inventory")

        # Load CSV data
        df_movies = pd.read_csv("movies_and_tv_collection.csv")
        df_games = pd.read_csv("board_and_card_games_collection.csv")
        df_kitchen = pd.read_csv("kitchen_gear_inventory_v2.csv")

        search_query = st.text_input("🔍 Search items across all categories...")

        tab_movies, tab_games, tab_kitchen = st.tabs(
            ["Movies & TV", "Board & Card Games", "Kitchen Gear"]
        )

        def display_cards(df, title_col, details_func, image_col="Image_Path"):
            if search_query:
                df = df[
                    df[title_col].astype(str).str.contains(search_query, case=False)
                ]

            cols = st.columns(3)
            for idx, row in df.reset_index(drop=True).iterrows():
                col = cols[idx % 3]
                with col:
                    with st.container(border=True):
                        # Display Image if available
                        img_val = row.get(image_col, "")
                        if pd.notna(img_val) and str(img_val).strip() != "":
                            st.image(str(img_val), use_container_width=True)
                        else:
                            st.caption("📷 No image uploaded")

                        st.subheader(row[title_col])
                        st.write(details_func(row))

        with tab_movies:
            display_cards(
                df_movies,
                "Title",
                lambda r: f"**Type:** {r.get('Type', '')} | **Rating:** {r.get('Rating', '')}\n\n**Year:** {r.get('Year Released', '')} | **Genre:** {r.get('Genre', '')}",
            )

        with tab_games:
            display_cards(
                df_games,
                "Title",
                lambda r: f"**Players:** {r.get('Number of Players', '')}\n\n**Length:** {r.get('Length of Play', '')} | **Age:** {r.get('Age Rating', '')}",
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