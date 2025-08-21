import asyncio
import discord
from discord.ext import commands
import sqlite3
import os
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import pytz
import re
import json
import functools
from PIL import Image, ImageDraw, ImageFont
import io

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Bot setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Create a scheduler
scheduler = AsyncIOScheduler()
uk_tz = pytz.timezone("Europe/London")

# Start the event loop and scheduler
async def main():
    scheduler.start()  # Start the scheduler
    print("Scheduler started")

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(main())

# Database setup
conn = sqlite3.connect('predictions.db')
cursor = conn.cursor()

# Create the matches table
cursor.execute('''
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team1 TEXT NOT NULL,
    team2 TEXT NOT NULL,
    match_type TEXT NOT NULL,
    match_date TEXT NOT NULL,  -- Date of the match
    match_week INTEGER NOT NULL,
    poll_created BOOLEAN DEFAULT FALSE, -- Track poll creation
    poll_message_id TEXT,
    winner TEXT,
    score TEXT,
    winner_points INTEGER DEFAULT 0,
    scoreline_points INTEGER DEFAULT 0
)
''')
conn.commit()

# Create the predictions table with foreign key reference to matches
cursor.execute('''
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    pred_winner TEXT,
    pred_score TEXT,
    match_id INTEGER,
    match_week INTEGER NOT NULL,
    points INTEGER DEFAULT 0,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    UNIQUE(user_id, match_id)
)
''')

# Commit changes to the database
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS bonus_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    match_week INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    answer TEXT NOT NULL,
    points INTEGER DEFAULT 0,
    UNIQUE(question_id, user_id),  -- Ensure one answer per user per question
    FOREIGN KEY (question_id) REFERENCES bonus_questions (id)
)
''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS bonus_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    description TEXT NOT NULL,
    options TEXT NOT NULL,
    required_answers INTEGER NOT NULL,
    correct_answer TEXT,
    date DATE,
    match_week INTEGER NOT NULL,
    poll_created BOOLEAN DEFAULT FALSE,
    points INTEGER NOT NULL,
    reaction_type TEXT
);

''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL
)
''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS leaderboard (
    user_id INTEGER,
    match_week INTEGER,
    weekly_points INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, match_week)
)
''')
conn.commit()

REACTION_SETS = {
    'set1': ['🟦', '🔵', '💙', '❤️', '🔴', '🟥'],  # Blue/Red themed emojis only
}

TOURNAMENT_STAGES = {
    1: ('Week 1', 1),
    2: ('Week 2', 2),
    3: ('Week 3', 3),
    4: ('Week 4', 4),
    5: ('Week 5', 5),
    6: ('Week 6', 6),
    7: ('Week 7', 7),
    8: ('Week 8', 8),
    9: ('Week 9', 9),
    10: ('Week 10', 10)
}

TEAM_EMOTES = {
    "KOI": "<:Koi:1330749930167603311>",
    "SK": "<:SK:1330750495169445928>",
    "FNC": "<:Fnatic:1330750485568946317>",
    "G2": "<:G2:1330750487762440222>",
    "TH": "<:Heretics:1330750491046445106>",
    "NAVI": "<:Rogue:1330750493839855686>",
    "VIT": "<:Vitality:1330750496557760624>",
    "GX": "<:GiantX:1330750489285103616>",
    "KC": "<:KC:1330750492552466463>",
    "BDS": "<:BDS:1330750484251938818>"
}

team_emote_ids = {
    "KOI": 1330749930167603311,
    "SK": 1330750495169445928,
    "FNC": 1330750485568946317,
    "G2": 1330750487762440222,
    "TH": 1330750491046445106,
    "NAVI": 1330750493839855686,
    "VIT": 1330750496557760624,
    "GX": 1330750489285103616,
    "KC": 1330750492552466463,
    "BDS": 1330750484251938818
}

def is_mod_channel(ctx):
    admin_channel_id = 1346615169433997322
    return ctx.channel.id == admin_channel_id

# Event: Bot ready
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def update_leaderboard():
    """
    Updates the leaderboard message in the dedicated channel.
    """
    try:
        leaderboard_channel_id = 1346615199544905730
        leaderboard_channel = bot.get_channel(leaderboard_channel_id)

        if not leaderboard_channel:
            print("Error: Leaderboard channel not found.")
            return

        # Fetch leaderboard data (sorted by match week)
        cursor.execute('''
            SELECT user_id, match_week, weekly_points
            FROM leaderboard
            ORDER BY match_week ASC
        ''')
        leaderboard_data = cursor.fetchall()

        if not leaderboard_data:
            leaderboard_message = "**🏆 Leaderboard 🏆**\n\nNo points have been awarded yet!"
        else:
            leaderboard_dict = {}
            user_id_list = set()

            # Organize leaderboard data by user_id and calculate total points dynamically
            for user_id, match_week, weekly_points in leaderboard_data:
                if user_id not in leaderboard_dict:
                    leaderboard_dict[user_id] = {"weeks": {}, "total": 0}
                leaderboard_dict[user_id]["weeks"][match_week] = weekly_points
                leaderboard_dict[user_id]["total"] += weekly_points  # Calculate total points dynamically
                user_id_list.add(user_id)

            # Fetch usernames from the users table
            cursor.execute(f'''
                SELECT user_id, username FROM users
                WHERE user_id IN ({",".join(["?"] * len(user_id_list))})
            ''', tuple(user_id_list))
            user_data = dict(cursor.fetchall())  # Map user_id -> username

            # Fetch the latest match week
            cursor.execute('SELECT MAX(match_week) FROM leaderboard')
            latest_week = cursor.fetchone()[0]
            print(f"latest_week: {latest_week}")

            def tie_breaker(user_data, latest_week):
                """
                Recursively break ties by comparing scores from previous weeks.
                """
                def compare_users(user1, user2, week):
                    if week < 1:
                        return 0  # No more weeks to compare, consider them equal

                    score1 = user_data[user1]["weeks"].get(week, 0)
                    score2 = user_data[user2]["weeks"].get(week, 0)

                    print(f"Comparing users {user1} and {user2} for week {week}: score1={score1}, score2={score2}")

                    if score1 != score2:
                        return score2 - score1  # Higher score first

                    return compare_users(user1, user2, week - 1)  # Compare previous week

                def sort_key(user):
                    return (user_data[user]["total"], user_data[user]["weeks"].get(latest_week, 0))

                def compare_with_previous_weeks(user1, user2):
                    # Start comparison from the week before latest_week
                    return compare_users(user1, user2, latest_week - 1)

                sorted_users = sorted(user_data.keys(), key=sort_key, reverse=True)
                print(f"sorted_users before tie-breaking: {sorted_users}")

                # Handle ties by recursively comparing scores from previous weeks
                i = 0
                while i < len(sorted_users) - 1:
                    j = i
                    while j < len(sorted_users) - 1 and sort_key(sorted_users[j]) == sort_key(sorted_users[j + 1]):
                        j += 1
                    if j > i:
                        # There is a tie between sorted_users[i:j+1]
                        tied_users = sorted_users[i:j + 1]
                        print(f"Tie detected between users: {tied_users}")
                        try:
                            # Sort tied users using previous weeks' scores
                            tied_users.sort(key=functools.cmp_to_key(compare_with_previous_weeks), reverse=True)
                            sorted_users[i:j + 1] = tied_users
                        except Exception as e:
                            print(f"Error during tie-breaking: {e}")
                    i = j + 1

                print(f"sorted_users after tie-breaking: {sorted_users}")
                return sorted_users

            # Usage in update_leaderboard function
            sorted_users = tie_breaker(leaderboard_dict, latest_week)
            chunks = []
            current_chunk = "**🏆 Leaderboard 🏆**\n\n"
            char_count = len(current_chunk)

            for rank, user_id in enumerate(sorted_users, start=1):
                data = leaderboard_dict[user_id]
                username = user_data.get(user_id)
                if not username:
                    try:
                        user = await bot.fetch_user(user_id)
                        username = user.name
                    except:
                        username = f"Unknown ({user_id})"

                week_scores = " | ".join(f"W{week}: {points}" for week, points in sorted(data["weeks"].items()))
                entry = f"{rank}. **{username}** - {week_scores} | **Total: {data['total']}**\n"

                # If adding this entry would exceed Discord's limit, start a new chunk
                if char_count + len(entry) > 1900:
                    chunks.append(current_chunk)
                    current_chunk = entry
                    char_count = len(entry)
                else:
                    current_chunk += entry
                    char_count += len(entry)

            # Add the final chunk
            if current_chunk:
                chunks.append(current_chunk)

            # Delete existing messages and send new ones
            await leaderboard_channel.purge(check=lambda m: m.author == bot.user)
            for chunk in chunks:
                await leaderboard_channel.send(chunk)

    except Exception as e:
        print(f"Error updating leaderboard: {e}")


@bot.command()
@commands.check(is_mod_channel)
async def schedule(ctx, match_date: str, match_type: str, team1: str, team2: str, winner_points: int = 0, scoreline_points:int = 0):
    """
    Schedule a match with a specific date.
    Args:
        match_date: Date of the match in DD-MM format.
        match_type: Type of match ('bo1', 'bo3', or 'bo5').
        team1: Name of the first team.
        team2: Name of the second team.
        winner/scoreline points: optional, will default to appropriate points if no value given
    """
    try:
        # Validate and parse match_date
        parsed_date = datetime.strptime(match_date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = parsed_date.replace(year=current_year)

        cursor.execute("SELECT match_date, match_week FROM matches ORDER BY match_date ASC")
        existing_matches = cursor.fetchall()

        match_week = 1  # Default to week 1 if no matches exist

        if existing_matches:
            last_match_date, last_match_week = existing_matches[-1]
            last_match_date = datetime.strptime(last_match_date, "%Y-%m-%d")

            # If new match is within 2 days of the last scheduled match, keep the same week
            if (match_date_with_year - last_match_date).days <= 2:
                match_week = last_match_week
            else:
                match_week = last_match_week + 1

        # Insert into the database with the full date and calculated match_week
        cursor.execute('''
        INSERT INTO matches (match_date, match_type, team1, team2, match_week, winner_points, scoreline_points)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (match_date_with_year.strftime("%Y-%m-%d"), match_type.upper(), team1, team2, match_week, winner_points, scoreline_points))
        conn.commit()

        await ctx.send(f"Match scheduled: {team1} vs {team2} on {match_date_with_year.strftime('%d-%m')} (Week {match_week})")

    except ValueError:
        await ctx.send("Invalid date format. Please use DD-MM.")

@bot.command()
@commands.check(is_mod_channel)
async def add_bonus_question(ctx, date: str, question: str, description: str, options: str, reaction_type: str = "numbers", required_answers: int = 1, points: int = 1):
    """
    Adds a bonus question to the database.
    Requires:
    Date in DD-MM format, the question (w/ quotation marks), any description (w/ quotation marks), options (list surrounded by quotation marks), required answers (will default to 1 if no value), points (default value 1)
    """
    try:
        parsed_date = datetime.strptime(date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = parsed_date.replace(year=current_year)

        if reaction_type.lower() not in ["numbers", "teams"]:
            await ctx.send("Invalid reaction type. Use 'numbers' or 'teams'")
            return

        cursor.execute("SELECT match_date, match_week FROM matches ORDER BY match_week DESC LIMIT 1")
        existing_matches = cursor.fetchall()

        match_week = 1  # Default to week 1 if no matches exist

        if existing_matches and len(existing_matches) > 0:
            last_match_date, last_match_week = existing_matches[0]
            last_match_date = datetime.strptime(last_match_date, "%Y-%m-%d")

            # If new match is within 2 days of the last scheduled match, keep the same week
            if (match_date_with_year - last_match_date).days <= 2:
                match_week = last_match_week
            else:
                match_week = last_match_week + 1

        cursor.execute('''
        INSERT INTO bonus_questions (date, question, description, options, required_answers, points, match_week, reaction_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (match_date_with_year.strftime("%Y-%m-%d"), question, description, options, required_answers, points, match_week, reaction_type))
        conn.commit()

        await ctx.send(f"Bonus question added for {date}: {question}")
    except Exception as e:
        await ctx.send(f"Error adding bonus question: {e}")


@bot.command()
@commands.check(is_mod_channel)
async def create_polls(ctx):
    """
    Creates prediction polls in a public channel and result polls in some mod channel type thing.
    """
    try:
        poll_channel_id = 1346615134885253181  # Replace with actual channel IDs        
        poll_channel = bot.get_channel(poll_channel_id)
        admin_channel_id = 1346615169433997322
        admin_channel = bot.get_channel(admin_channel_id)
        # Fetch matches that have not had polls created yet
        cursor.execute('''
        SELECT id, match_date, match_type, team1, team2, winner_points, scoreline_points
        FROM matches
        WHERE poll_created = FALSE
        ORDER BY match_date
        ''')
        matches = cursor.fetchall()

        # Fetch bonus questions that have not had polls created yet
        cursor.execute('''
        SELECT id, date, question, description, options, points, reaction_type
        FROM bonus_questions
        WHERE poll_created = FALSE
        ORDER BY date
        ''')
        bonus_questions = cursor.fetchall()

        if not matches and not bonus_questions:
            await ctx.send("No matches or bonus questions without polls!")
            return

        if not poll_channel or not admin_channel:
            await ctx.send("Error: One or both channels could not be found.")
            return

        # Track the current date for grouping matches and questions
        current_date = None

        # --- Create polls for matches ---
        for match in matches:
            match_id, match_date, match_type, team1, team2, winner_points, scoreline_points = match

            reaction_set = 'set1'

            if isinstance(match_date, str):
                match_date = datetime.strptime(match_date, "%Y-%m-%d").date()

            # Add date header if the date changes
            if match_date != current_date:
                current_date = match_date
                formatted_date = current_date.strftime("%d/%m/%Y")
                await poll_channel.send(f"**{formatted_date} Games**")
                await admin_channel.send(f"**{formatted_date} Games**")

            # Generate score options based on match type
            if match_type == 'BO1':
                options = [f"{team1} wins", f"{team2} wins"]
                reactions = [REACTION_SETS[reaction_set][0], REACTION_SETS[reaction_set][5]]  # Outer pair (🟦🟥)
            elif match_type == 'BO3':
                options = [f"{team1} 2-0", f"{team1} 2-1", f"{team2} 2-1", f"{team2} 2-0"]
                reactions = [                                                    # From outside in:
                    REACTION_SETS[reaction_set][0],  # 🟦 (far left)
                    REACTION_SETS[reaction_set][1],  # 🟥 (far right)
                    REACTION_SETS[reaction_set][4],  # 🔵 (inner left)
                    REACTION_SETS[reaction_set][5]   # 🔴 (inner right)
                ]
            elif match_type == 'BO5':
                options = [
                    f"{team1} 3-0", f"{team1} 3-1", f"{team1} 3-2",
                    f"{team2} 3-2", f"{team2} 3-1", f"{team2} 3-0"
                ]
                reactions = REACTION_SETS[reaction_set]  # All six emojis

            # Create prediction and result polls for the match
            await create_match_poll(poll_channel, admin_channel, match_id, match_date, team1, team2, match_type, options, reactions, winner_points, scoreline_points)

        # --- Create polls for bonus questions ---
        for question in bonus_questions:
            question_id, match_date, question_text, description, options, point_value, reaction_type = question

            if isinstance(match_date, str):
                match_date = datetime.strptime(match_date, "%Y-%m-%d").date()

            option_split = [option.strip() for option in options.split(",")]

            if reaction_type.lower() == "teams":
                # Try to get emotes for each team
                reactions = []
                for team in option_split:
                    if team in TEAM_EMOTES:
                        reactions.append(TEAM_EMOTES[team])
                    else:
                        await ctx.send(f"❌ No emote found for team: {team}")
                        return
            else:
                NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
                reactions = NUMBER_EMOJIS[:len(option_split)]

            # Add date header if the date changes
            if match_date != current_date:
                current_date = match_date
                formatted_date = current_date.strftime("%d/%m/%Y")
                await poll_channel.send(f"**{formatted_date} Bonus Questions**")
                await admin_channel.send(f"**{formatted_date} Bonus Questions**")

            # Create prediction and result polls for the bonus question
            await create_bonus_poll(poll_channel, admin_channel, question_id, question_text, description, option_split, reactions, point_value)

        await ctx.send("Polls successfully created for all pending matches and bonus questions.")

    except Exception as e:
        await ctx.send(f"Error creating polls: {e}")


async def create_match_poll(prediction_channel, result_channel, match_id, match_date, team1, team2, match_type, options, reactions, winner_points, scoreline_points):
    """
    Helper function to create match polls.
    """
    # Create prediction poll
    prediction_embed = discord.Embed(
        title=f"Match Poll: {team1} vs {team2} ({match_type})",
        description=f"Match Date: {match_date}\nReact with your prediction!",
        color=discord.Color.blue()
    )
    for i, option in enumerate(options):
        prediction_embed.add_field(name=f"Option {reactions[i]}", value=option, inline=False)

    prediction_message = await prediction_channel.send(embed=prediction_embed)
    for reaction in reactions:
        await prediction_message.add_reaction(reaction)

    # Create result poll
    result_embed = discord.Embed(
        title=f"Result Poll: {team1} vs {team2} ({match_type})",
        description=f"Match Date: {match_date}\nReact with the correct result!",
        color=discord.Color.green()
    )
    for i, option in enumerate(options):
        result_embed.add_field(name=f"Option {reactions[i]}", value=option, inline=False)

    result_message = await result_channel.send(embed=result_embed)
    for reaction in reactions:
        await result_message.add_reaction(reaction)

    # Update poll_created to True
    cursor.execute('''
    UPDATE matches
    SET poll_created = TRUE
    WHERE id = ?
    ''', (match_id,))
    conn.commit()


async def create_bonus_poll(prediction_channel, result_channel, question_id, question_text, description, options, reactions, points):
    """
    Helper function to create bonus question polls.
    """
    # Create prediction poll
    prediction_embed = discord.Embed(
        title=f"Bonus Question: {question_text}",
        description=description,
        color=discord.Color.gold()
    )
    for i, option in enumerate(options, start=1):
        prediction_embed.add_field(name=f"Option {i}", value=option, inline=False)

    prediction_message = await prediction_channel.send(embed=prediction_embed)
    for reaction in reactions:
        await prediction_message.add_reaction(reaction)

    # Create result poll
    result_embed = discord.Embed(
        title=f"Bonus Question Result: {question_text}",
        description=description + (f" Points: {points}"),
        color=discord.Color.orange()
    )
    for i, option in enumerate(options, start=1):
        result_embed.add_field(name=f"Option {i}", value=option, inline=False)

    result_message = await result_channel.send(embed=result_embed)
    for reaction in reactions:
        await result_message.add_reaction(reaction)

    # Update poll_created to True
    cursor.execute('''
    UPDATE bonus_questions
    SET poll_created = TRUE
    WHERE id = ?
    ''', (question_id,))
    conn.commit()


@bot.event
async def on_raw_reaction_add(payload):
    try:
        POLL_CHANNEL_ID = 1346615134885253181  # Your poll channel
        ADMIN_CHANNEL_ID = 1346615169433997322  # Your admin channel
        if payload.user_id == bot.user.id:
            return  # Ignore bot reactions
        if payload.channel_id not in [POLL_CHANNEL_ID, ADMIN_CHANNEL_ID]:
            return
        print("the bot has seen the reaction")
        bot_channel_id = 1346615855408091180  # Replace with your bot channel ID
        bot_channel = bot.get_channel(bot_channel_id)
        
        if payload.user_id == bot.user.id:
            return

        # Fetch the full message if not already cached

        channel = bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        user = await bot.fetch_user(payload.user_id)
            
        if not message.embeds:
            return
            

        # Ensure the message has an embed

        embed = message.embeds[0]  # Get the first embed
        title = embed.title  # Embed title (e.g., "Match Poll: TSM vs FTX (BO5)")
        description = embed.description  # Message content for match date

        # Parse poll type and match details from the title
        if "Match Poll" in title:
            poll_type = "match_poll"
        elif "Result Poll" in title:
            poll_type = "result_poll"
        elif "Bonus Question Result" in title:
            poll_type = "bonus_result"
        elif "Bonus Question" in title:
            poll_type = "bonus_poll"
        else:
            return  # Ignore unrelated embeds

        if poll_type == "match_poll" or poll_type == "result_poll":
            # Extract team names and match type from the title
            try:
                match_details = title.split(":")[1].strip()  # e.g., "TSM vs FTX (BO5)"
                teams, match_type = match_details.rsplit("(", 1)
                team1, team2 = [team.strip() for team in teams.split("vs")]
                match_type = match_type.strip(")")
                datestring = re.search(r"Match Date:\s*(\d{4}-\d{2}-\d{2})", description)
            except ValueError:
                await message.channel.send("Error parsing match details from poll.")
                return

            # Extract match date (if needed, fallback to current date)
            try:
                if description:
                    match_date = datestring.group(1)  # Extracted date in YYYY-MM-DD format
                else:
                    match_date = datetime.now().date().isoformat()  # Fallback to current date if no match found
            except Exception:
                await message.channel.send("Error parsing match date.")
                return

            # Locate the match in the database
            cursor.execute('''
            SELECT id, match_week, winner_points, scoreline_points FROM matches
            WHERE team1 = ? AND team2 = ? AND match_type = ? AND match_date = ?
            ''', (team1, team2, match_type, match_date))
            match_row = cursor.fetchone()

            if not match_row:
                await message.channel.send(f"No match found for {team1} vs {team2} ({match_type}) on {match_date}.")
                return

            match_id = match_row[0]  # Match ID in the database
            scoreline_points = match_row[3]
            winner_points = match_row[2]


            # Determine which action to take based on poll type
            if poll_type == "match_poll":
                cursor.execute('''
                    SELECT match_week
                    FROM (
                        SELECT match_week FROM predictions WHERE user_id = ?
                        UNION
                        SELECT match_week FROM bonus_answers WHERE user_id = ?
                    )
                    ORDER BY match_week DESC
                    LIMIT 1
                ''', (user.id, user.id))
                latest_stage_row = cursor.fetchone()
                latest_stage = latest_stage_row[0] if latest_stage_row and latest_stage_row[0] is not None else 0
                latest_stage_value = TOURNAMENT_STAGES.get(latest_stage, [None, 0])[1] if latest_stage else 0
                print(f"Latest stage for user: {latest_stage_value}")

                current_stage_value = TOURNAMENT_STAGES[match_row[1]][1]  # Gets the numeric value (1 for 'G', 2 for 'SF', 3 for 'F')
                
                # Get the current match's stage
                current_stage = TOURNAMENT_STAGES[match_row[1]][1]  # Get stage number from match_week
                
                # Generate list of missed stages
                missed_stages = []
                if latest_stage_value < current_stage_value:
                    for stage_value in range(latest_stage_value + 1, current_stage_value):
                        # Find the stage key (G/SF/F) for this value
                        stage_key = next((k for k, v in TOURNAMENT_STAGES.items() if v[1] == stage_value), None)
                        if stage_key:
                            missed_stages.append(stage_key)
                
                print(f"Missed stages: {missed_stages}")

                if missed_stages:
                    for stage in missed_stages:
                        # Get the lowest total points for this stage (excluding The Coin)
                        cursor.execute('''
                            SELECT MIN(weekly_points) 
                            FROM leaderboard 
                            WHERE match_week = ? 
                            AND user_id NOT IN (SELECT user_id FROM users WHERE username = 'The Coin')
                        ''', (stage,))
                        
                        lowest_score_row = cursor.fetchone()
                        lowest_score = lowest_score_row[0] if lowest_score_row and lowest_score_row[0] is not None else 0

                        # Insert or update leaderboard entry
                        cursor.execute('''
                            INSERT INTO leaderboard (user_id, match_week, weekly_points)
                            VALUES (?, ?, ?)
                            ON CONFLICT(user_id, match_week) DO UPDATE SET 
                                weekly_points = excluded.weekly_points
                        ''', (user.id, stage, lowest_score))
                        conn.commit()
                
                # Handle match poll (log predictions)
                options = [field.value for field in embed.fields]
                all_possible_reactions = set()
                for reaction_set in REACTION_SETS.values():
                    all_possible_reactions.update(reaction_set)

                if str(payload.emoji.name) not in all_possible_reactions:
                    await bot_channel.send(f"{user.mention} Invalid reaction. Please select a valid option.")
                    return
                
                used_reaction_set = None
                for set_name, reactions in REACTION_SETS.items():
                    if str(payload.emoji.name) in reactions:
                        used_reaction_set = set_name
                        break

                if used_reaction_set is None:
                    await bot_channel.send(f"{user.mention} Invalid reaction type.")
                    return

                # Generate score options based on match type
                if match_type == 'BO1':
                    options = [f"{team1} wins", f"{team2} wins"]
                    reactions = (REACTION_SETS[used_reaction_set][0], REACTION_SETS[used_reaction_set][5])  # Outer pair (🟦🟥)
                elif match_type == 'BO3':
                    options = [f"{team1} 2-0", f"{team1} 2-1", f"{team2} 2-1", f"{team2} 2-0"]
                    reactions = (                                                    # From outside in:
                        REACTION_SETS[used_reaction_set][0],  # 🟦 (far left)
                        REACTION_SETS[used_reaction_set][1],  # 🟥 (far right)
                        REACTION_SETS[used_reaction_set][4],  # 🔵 (inner left)
                        REACTION_SETS[used_reaction_set][5]   # 🔴 (inner right)
                    )
                elif match_type == 'BO5':
                    options = [
                        f"{team1} 3-0", f"{team1} 3-1", f"{team1} 3-2",
                        f"{team2} 3-2", f"{team2} 3-1", f"{team2} 3-0"
                    ]
                    reactions = REACTION_SETS[used_reaction_set][:6]  # All six emojis

                # Now we can safely get the index
                if str(payload.emoji.name) in reactions:  # Check if it's in the correct subset
                    selected_index = reactions.index(str(payload.emoji.name))
                    prediction = options[selected_index]
                    pred_winner, pred_score = prediction.split(" ", 1)
                else:
                    await bot_channel.send(f"{user.mention} Invalid reaction for this match type.")
                    return

                # Insert prediction into the database
                cursor.execute('''
                INSERT INTO predictions (match_id, match_week, user_id, pred_winner, pred_score, points)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(match_id, user_id) DO UPDATE SET
                pred_winner = excluded.pred_winner,
                pred_score = excluded.pred_score
                ''', (match_id, match_row[1], user.id, pred_winner, pred_score))
                conn.commit()

                cursor.execute('''
                INSERT INTO users (user_id, username)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
                ''', (user.id, str(user.name)))  # Stores the current username
                conn.commit()

                print(f"{user.name} your prediction has been logged: {pred_winner} with score {pred_score}.")

            elif poll_type == "result_poll":
                # Check if result already exists
                cursor.execute('''
                SELECT winner, score FROM matches
                WHERE id = ? AND winner IS NOT NULL
                ''', (match_id,))
                existing_result = cursor.fetchone()
                
                if existing_result:
                    await message.channel.send("Result has already been recorded for this match.")
                    return

                # Handle result poll
                options = [field.value for field in embed.fields]
                all_possible_reactions = set()
                for reaction_set in REACTION_SETS.values():
                    all_possible_reactions.update(reaction_set)

                if str(payload.emoji.name) not in all_possible_reactions:
                    return

                used_reaction_set = None
                for set_name, reactions in REACTION_SETS.items():
                    if str(payload.emoji.name) in reactions:
                        used_reaction_set = set_name
                        break
                if used_reaction_set is None:
                    await message.channel.send("Invalid reaction type")
                    return

                if match_type == 'BO1':
                    options = [f"{team1} wins", f"{team2} wins"]
                    reactions = (REACTION_SETS[used_reaction_set][0], REACTION_SETS[used_reaction_set][5])  # Outer pair (🟦🟥)
                elif match_type == 'BO3':
                    options = [f"{team1} 2-0", f"{team1} 2-1", f"{team2} 2-1", f"{team2} 2-0"]
                    reactions = (                                                    # From outside in:
                        REACTION_SETS[used_reaction_set][0],  # 🟦 (far left)
                        REACTION_SETS[used_reaction_set][1],  # 🟥 (far right)
                        REACTION_SETS[used_reaction_set][4],  # 🔵 (inner left)
                        REACTION_SETS[used_reaction_set][5]   # 🔴 (inner right)
                    )
                elif match_type == 'BO5':
                    options = [
                        f"{team1} 3-0", f"{team1} 3-1", f"{team1} 3-2",
                        f"{team2} 3-2", f"{team2} 3-1", f"{team2} 3-0"
                    ]
                    reactions = REACTION_SETS[used_reaction_set][:6]  # All six emojis

                # 5. Get selected result
                if str(payload.emoji.name) in reactions:
                    selected_index = reactions.index(str(payload.emoji.name))
                    result = options[selected_index]
                    winner, score = result.split(" ", 1)
                else:
                    await message.channel.send("Invalid reaction for this match type")
                    return

                # Update match result in the database
                cursor.execute('''
                UPDATE matches
                SET winner = ?, score = ?
                WHERE id = ?
                ''', (winner, score, match_id))
                conn.commit()

                # Award points for correct predictions
                cursor.execute('''
                SELECT id, user_id, match_week, pred_winner, pred_score
                FROM predictions
                WHERE match_id = ?
                ''', (match_id,))
                predictions = cursor.fetchall()

                for prediction in predictions:
                    pred_id, user_id, match_week, pred_winner, pred_score = prediction
                    points = 0

                    # Award points for correct winner
                    if pred_winner == winner:
                        if match_row[2] != 0:
                            points += match_row[2] 
                        else:
                            points += 1 if match_type == "BO1" else (2 if match_type == "BO3" else 3)

                        # Bonus points for correct score
                        if pred_score == score:
                            if match_row[3] != 0:
                                points += match_row[3]
                            else:
                                points += 1 if match_type == "BO3" else (2 if match_type == "BO5" else 0)

                    # Update points in the predictions table
                    cursor.execute('''
                    UPDATE predictions
                    SET points = points + ?
                    WHERE id = ?
                    ''', (points, pred_id))

                    cursor.execute('''
                    INSERT INTO leaderboard (user_id, match_week, weekly_points)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, match_week) DO UPDATE SET
                        weekly_points = leaderboard.weekly_points + ?
                    ''', (user_id, match_week, points, points))
                    conn.commit()

                conn.commit()

                await update_leaderboard()

                await message.channel.send(
                    f"Result recorded for match {team1} vs {team2} ({match_type}): {winner} wins with score {score}! Points have been awarded."
                )
        elif poll_type == "bonus_poll" or poll_type == "bonus_result":
            # Locate the question in the database
            question_text = title.split(":")[1].strip()  # Extract question text
            cursor.execute('''
            SELECT id, match_week, options, reaction_type, required_answers, points FROM bonus_questions
            WHERE question = ?
            ORDER BY id DESC LIMIT 1
            ''', (question_text,))
            question_row = cursor.fetchone()

            if not question_row:
                await message.channel.send(f"Error: No bonus question found for '{question_text}'.")
                return

            question_id, week, options, reaction_type, required_answers, points_value = question_row
            option_split = [option.strip() for option in options.split(",")]
            reactions = []
            if reaction_type == "numbers":
                reactions = [f"{i + 1}️⃣" for i in range(len(option_split))]
                reaction_ids = [str(i + 1) for i in range(len(option_split))]
            elif reaction_type == "teams":
                    option_teams = [team.strip() for team in options.split(",")]
                    # Try to get emotes for each team
                    reactions = []
                    for team in option_teams:
                        if team in TEAM_EMOTES:
                            reactions.append(TEAM_EMOTES[team])
                            reaction_ids = [TEAM_EMOTES[team].split(':')[2].rstrip('>') for team in option_teams]
            
            print(f"Reactions: {reactions}")
            print(f"reaction_ids: {reaction_ids}")
            print(f"payload emoji id: {payload.emoji.id}")
            print(f"payload emoji name: {payload.emoji.name}")

            if (str(payload.emoji.name) not in reactions) and (str(payload.emoji.id) not in reaction_ids) and (str(payload.emoji.name) != "✅"):
                await message.channel.send("Invalid reaction. Please select a valid option.")
                return

            if poll_type == "bonus_poll":
                cursor.execute('''
                    SELECT MAX(match_week) FROM (
                        SELECT match_week FROM predictions WHERE user_id = ?
                        UNION
                        SELECT match_week FROM bonus_answers WHERE user_id = ?
                    )
                ''', (user.id, user.id))
                latest_week = cursor.fetchone()[0] or 0
                print(latest_week)
                
                if latest_week is None:
                    latest_week = 0  # No previous activity

                # Find all missed weeks between the latest activity and current match week
                missed_weeks = list(range(latest_week + 1, question_row[1]))
                print(f"all_weeks: {missed_weeks}")

                if missed_weeks:
                    for week in missed_weeks:
                        # Get the lowest total points for this match week (excluding The Coin)
                        cursor.execute('''
                            SELECT MIN(weekly_points) 
                            FROM leaderboard 
                            WHERE match_week = ? 
                            AND user_id NOT IN (SELECT user_id FROM users WHERE username = 'The Coin')
                        ''', (week,))
                        
                        lowest_score_row = cursor.fetchone()
                        print(lowest_score_row)
                        lowest_score = lowest_score_row[0] if lowest_score_row else 0  # Ensure no NoneType error

                        # Insert or update leaderboard entry
                        cursor.execute('''
                            INSERT INTO leaderboard (user_id, match_week, weekly_points)
                            VALUES (?, ?, ?)
                            ON CONFLICT(user_id, match_week) DO UPDATE SET 
                                weekly_points = excluded.weekly_points
                        ''', (user.id, week, lowest_score))
                        conn.commit()
                
                # Log reactions and options to debug
                print(f"Reactions: {reactions}")
                print(f"Options: {options}")

                try:
                    # Ensure reactions are aligned with options
                    try:
                        if isinstance(payload.emoji, discord.PartialEmoji):
                            # Handle custom emojis
                            selected_index = reaction_ids.index(str(payload.emoji.id))
                        elif isinstance(payload.emoji, discord.Emoji):
                            # Handle standard emojis
                            selected_index = reactions.index(str(payload.emoji))
                    except ValueError:
                        await bot_channel.send(f"{user.mention} Invalid reaction. Please select a valid option.")
                        return
                    print(selected_index)

                    # Fetch existing answers
                    cursor.execute('''
                    SELECT answer FROM bonus_answers WHERE user_id = ? AND question_id = ?
                    ''', (user.id, question_id))
                    existing_answer_row = cursor.fetchone()
                    print(existing_answer_row)

                    if existing_answer_row:
                        try:
                            existing_answers = json.loads(existing_answer_row[0])  # Parse JSON
                            if not isinstance(existing_answers, list):  # Ensure it's a list
                                existing_answers = []
                        except json.JSONDecodeError:
                            existing_answers = []  # Reset if data is corrupted
                    else:
                        existing_answers = []  # First-time user, initialize empty list

                    print(existing_answers)

                    if len(existing_answers) < required_answers:
                        # Map emoji to actual option
                        selected_option = option_split[selected_index]

                        if selected_option not in existing_answers:
                            existing_answers.append(selected_option)  # Add selection


                        updated_answers = json.dumps(existing_answers)

                        cursor.execute('''
                        INSERT INTO bonus_answers (user_id, question_id, answer, match_week)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, question_id) DO UPDATE SET answer = excluded.answer
                        ''', (user.id, question_id, updated_answers, question_row[1]))
                        conn.commit()
                        
                        cursor.execute('''
                        INSERT INTO users (user_id, username)
                        VALUES (?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
                        ''', (user.id, str(user.name)))  # Stores the current username
                        conn.commit()
                    else:
                        await bot_channel.send(f"{user.mention} You have already selected an answer. Please remove one first if you wish to change your answer.")
                        return
                    
                except ValueError:
                    # Handle cases where the emoji is not in the reactions list
                    return


            elif poll_type == "bonus_result":
                cursor.execute('''
                SELECT correct_answer FROM bonus_questions
                WHERE question = ?
                ORDER BY id DESC LIMIT 1
                ''', (question_text,))
                answer_row = cursor.fetchone()

                cursor.execute('SELECT match_week, points FROM bonus_questions WHERE id = ?', (question_id,))
                bonus_data = cursor.fetchone()  # Get match_week from the bonus question
                if not bonus_data:
                    return
                match_week, points = bonus_data
                
                if not question_row:

                    await message.channel.send(f"Error: No bonus question found for '{question_text}'.")
                    return

                if answer_row and answer_row[0]:  # Ensure it is not None or empty
                    try:
                        correct_answers = set(json.loads(answer_row[0]))  # Parse stored JSON
                    except json.JSONDecodeError:
                        print(f"Error parsing JSON from DB: {answer_row[0]}")  # Debugging
                        correct_answers = set()
                else:
                    correct_answers = set()  # Initialize as empty if no value is stored

                if isinstance(payload.emoji, discord.PartialEmoji):
                    # Handle custom emojis
                    user_input = dict(zip(reaction_ids, option_split)).get(str(payload.emoji.id), None)
                elif isinstance(payload.emoji, discord.Emoji):
                    user_input = dict(zip(reactions, option_split)).get(str(payload.emoji.name), None)
                
                print(user_input)

                if user_input:
                    correct_answers.add(user_input)  # Add selection

                    correct_answers_json = json.dumps(list(correct_answers))
                    cursor.execute('''
                    UPDATE bonus_questions
                    SET correct_answer = ?
                    WHERE id = ?
                    ''', (correct_answers_json, question_id))
                    conn.commit()
                    await message.channel.send(f"✅ The correct answer for '{question_text}' has been recorded.")
                    return

                if str(payload.emoji.name) == "✅":  # Change this emoji to whatever you prefer
                    await message.channel.send(f"✅ Correct answer selection finalized! Checking responses...")
                    print(correct_answers)
                    # Fetch user responses
                    cursor.execute('''
                    SELECT user_id, answer FROM bonus_answers
                    WHERE question_id = ?
                    ''', (question_id,))
                    user_responses = cursor.fetchall()

                    if not user_responses:
                        await message.channel.send("Error: No user responses found for this bonus question.")
                        return

                    awarded_users = []
                    for user_id, user_selections_json in user_responses:
                        user_selections = set(json.loads(user_selections_json))
                        print(user_selections)  # Convert user answers

                        if len(correct_answers) == required_answers:
                            # For exact number of required answers, need exact match
                            points_awarded = points_value if user_selections == correct_answers else 0
                            print(f"Exact match required. Match found: {user_selections == correct_answers}")
                        else:
                            # For more answers than required, must be valid subset AND have correct number of answers
                            if len(user_selections) == required_answers and user_selections.issubset(correct_answers):
                                points_awarded = points_value
                            else:
                                points_awarded = 0
                            print(f"Subset check: selections={len(user_selections)}, required={required_answers}, valid subset={user_selections.issubset(correct_answers)}")

                        # Award points
                        cursor.execute('''
                        UPDATE bonus_answers
                        SET points = ?
                        WHERE question_id = ? AND user_id = ?
                        ''', (points_awarded, question_id, user_id))
                        conn.commit()

                        cursor.execute('''
                        INSERT INTO leaderboard (user_id, match_week, weekly_points)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, match_week) DO UPDATE SET
                            weekly_points = leaderboard.weekly_points + ?
                        ''', (user_id, match_week, points_awarded, points_awarded))
                        conn.commit()

                        if points_awarded > 0:
                            awarded_users.append(user_id)

                    # --- **Send Final Result Message** ---
                    correct_answer_text = ", ".join(correct_answers)
                    if awarded_users:
                        await message.channel.send(f"Points awarded! The correct answer was: {correct_answer_text}.")
                    else:
                        await message.channel.send(f"No users selected the correct answer. The correct answer was: {correct_answer_text}.")

                    await update_leaderboard()
    except Exception as e:
        print(f"Error in reaction handling: {e}")
        if bot_channel:
            await bot_channel.send(f"Error processing reaction: {e}")

@bot.event
async def on_raw_reaction_remove(payload):
    print("hi")
    POLL_CHANNEL_ID = 1346615134885253181  # Your poll channel
    ADMIN_CHANNEL_ID = 1346615169433997322  # Your admin channel
    if payload.user_id == bot.user.id:
        return  # Ignore bot reactions
    if payload.channel_id not in [POLL_CHANNEL_ID, ADMIN_CHANNEL_ID]:
        return

    # Fetch the full message if not already cached
    try:
        channel = bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        user = await bot.fetch_user(payload.user_id)

        if not message.embeds:
            return

        embed = message.embeds[0]
        title = embed.title
    
        if not message.embeds:
            return

        if "Bonus Question" in title:
            question_text = title.split(":")[1].strip()
            cursor.execute('''
                SELECT id, options, reaction_type, match_week FROM bonus_questions
                WHERE question = ?
            ''', (question_text,))
            question_row = cursor.fetchone()

            question_id, options, reaction_type, match_week = question_row
            

            if not question_row:
                return

            option_split = [option.strip() for option in options.split(",")]
            reactions = []
            if reaction_type == "numbers":
                reactions = [f"{i + 1}️⃣" for i in range(len(option_split))]
            elif reaction_type == "teams":
                    option_teams = [team.strip() for team in options.split(",")]
                    # Try to get emotes for each team
                    reactions = []
                    for team in option_teams:
                        if team in TEAM_EMOTES:
                            reactions.append(TEAM_EMOTES[team])
                            reaction_ids = [TEAM_EMOTES[team].split(':')[2].rstrip('>') for team in option_teams]

            if str(payload.emoji.name) not in reactions and str(payload.emoji.id) not in reaction_ids:
                return

            # Fetch existing answers
            cursor.execute('''
            SELECT answer FROM bonus_answers WHERE user_id = ? AND question_id = ?
            ''', (user.id, question_id))
            existing_answer_row = cursor.fetchone()

            if existing_answer_row and existing_answer_row[0]:
                existing_answers = json.loads(existing_answer_row[0])
                print(existing_answers)
            else:
                return  # Nothing to remove

            # Map emoji to actual option
            if isinstance(payload.emoji, discord.PartialEmoji):
                            # Handle custom emojis
                selected_index = reaction_ids.index(str(payload.emoji.id))
            elif isinstance(payload.emoji, discord.Emoji):
                            # Handle standard emojis
                selected_index = reactions.index(str(payload.emoji.name))
            selected_option = option_split[selected_index]

            if selected_option in existing_answers:
                print("i got here :/)")
                existing_answers.remove(selected_option)
                print(existing_answers)  # Remove selection

            updated_answers = json.dumps(existing_answers)

            # Update the database
            cursor.execute('''
            UPDATE bonus_answers
            SET answer = ?
            WHERE user_id = ? AND question_id = ?
            ''', (updated_answers, user.id, question_id))
            conn.commit()

        elif "Bonus Result" in title:
            question_text = title.split(":")[1].strip()
            cursor.execute('''
            SELECT id, options, reaction_type, correct_answer, match_week FROM bonus_questions
            WHERE question = ?
            ''', (question_text,))
            question_row = cursor.fetchone()
            
            if not question_row:
                return
        
            question_id, options, reaction_type, answer_data, match_week = question_row

            option_split = [option.strip() for option in options.split(",")]
            if reaction_type == "numbers":
                reactions = [f"{i + 1}️⃣" for i in range(len(option_split))]
            elif reaction_type == "teams":
                    # Try to get emotes for each team
                reactions = []
                for team in option_split:
                    if team in TEAM_EMOTES:
                        reactions.append(TEAM_EMOTES[team])

            # Only proceed if we haven't awarded points yet
            if str(payload.emoji.name) == "✅":  # If the finalize emoji
                cursor.execute('BEGIN TRANSACTION')
                try:
                    # Get all users who got points for this question
                    cursor.execute('''
                    SELECT user_id, points FROM bonus_answers 
                    WHERE question_id = ? AND points > 0
                    ''', (question_id,))
                    awarded_users = cursor.fetchall()

                    # Remove points from bonus_answers
                    cursor.execute('''
                    UPDATE bonus_answers 
                    SET points = 0 
                    WHERE question_id = ?
                    ''', (question_id,))

                    # Remove points from leaderboard
                    for user_id, points in awarded_users:
                        cursor.execute('''
                        UPDATE leaderboard 
                        SET weekly_points = weekly_points - ? 
                        WHERE user_id = ? AND match_week = ?
                        ''', (points, user_id, match_week))

                    # Clear the correct answer
                    cursor.execute('''
                    UPDATE bonus_questions
                    SET correct_answer = NULL 
                    WHERE id = ?
                    ''', (question_id,))
                    cursor.execute('COMMIT')
                    await update_leaderboard()
                    return
                except Exception as e:
                    cursor.execute('ROLLBACK')
                    print(f"Error during transaction: {e}")
                    return

            elif str(payload.emoji.name) in reactions:
        # Only proceed if tick is not present
                if not any(r.emoji == "✅" for r in message.reactions):
                    if answer_data:
                        correct_answers = set(json.loads(answer_data))
                        selected_option = dict(zip(reactions, option_split)).get(str(payload.emoji.name))
                            
                        if selected_option and selected_option in correct_answers:
                            correct_answers.remove(selected_option)
                            # Update database with new correct answers
                            correct_answers_json = json.dumps(list(correct_answers))
                            cursor.execute('''
                            UPDATE bonus_questions
                            SET correct_answer = ?
                            WHERE id = ?
                            ''', (correct_answers_json, question_id))
                            conn.commit()

        
        
        elif "Match Poll" in title:
            try:
                match_details = title.split(":")[1].strip()  # Extract teams and match type
                teams, match_type = match_details.rsplit("(", 1)
                team1, team2 = [team.strip() for team in teams.split("vs")]
                match_type = match_type.strip(")")

                # Extract Match Date
                match_date_line = embed.description.split("\n")[0]  # "Match Date: YYYY-MM-DD"
                match_date = match_date_line.replace("Match Date: ", "").strip()

                # Locate Match in DB
                cursor.execute('''
                SELECT id FROM matches
                WHERE team1 = ? AND team2 = ? AND match_type = ? AND match_date = ?
                ''', (team1, team2, match_type, match_date))
                match_row = cursor.fetchone()

                if not match_row:
                    return  # No match found, nothing to remove

                match_id = match_row[0]

                # Handle reaction validation
                all_possible_reactions = set()
                for reaction_set in REACTION_SETS.values():
                    all_possible_reactions.update(reaction_set)

                if str(payload.emoji.name) not in all_possible_reactions:
                    return

                # Find which reaction set was used
                used_reaction_set = None
                for set_name, reactions in REACTION_SETS.items():
                    if str(payload.emoji.name) in reactions:
                        used_reaction_set = set_name
                        break

                if not used_reaction_set:
                    return

                # Generate score options based on match type
                if match_type == 'BO1':
                    options = [f"{team1} wins", f"{team2} wins"]
                    reactions = (REACTION_SETS[used_reaction_set][0], REACTION_SETS[used_reaction_set][5])  # Outer pair (🟦🟥)
                elif match_type == 'BO3':
                    options = [f"{team1} 2-0", f"{team1} 2-1", f"{team2} 2-1", f"{team2} 2-0"]
                    reactions = (                                                    # From outside in:
                        REACTION_SETS[used_reaction_set][0],  # 🟦 (far left)
                        REACTION_SETS[used_reaction_set][1],  # 🟥 (far right)
                        REACTION_SETS[used_reaction_set][4],  # 🔵 (inner left)
                        REACTION_SETS[used_reaction_set][5]   # 🔴 (inner right)
                    )
                elif match_type == 'BO5':
                    options = [
                        f"{team1} 3-0", f"{team1} 3-1", f"{team1} 3-2",
                        f"{team2} 3-2", f"{team2} 3-1", f"{team2} 3-0"
                    ]
                    reactions = REACTION_SETS[used_reaction_set][:6]  # All six emojis

                # Get the prediction that corresponds to the removed reaction
                if str(payload.emoji.name) in reactions:
                    selected_index = reactions.index(str(payload.emoji.name))
                    prediction = options[selected_index]
                    pred_winner, pred_score = prediction.split(" ", 1)

                    # Only delete if the stored prediction matches the removed reaction
                    cursor.execute('''
                    DELETE FROM predictions 
                    WHERE match_id = ? AND user_id = ? 
                    AND pred_winner = ? AND pred_score = ?
                    ''', (match_id, user.id, pred_winner, pred_score))
                    conn.commit()


            except Exception as e:
                print(f"Error removing match prediction: {e}")
        elif "Result Poll" in title:
            try:
                match_details = title.split(":")[1].strip()
                teams, match_type = match_details.rsplit("(", 1)
                team1, team2 = [team.strip() for team in teams.split("vs")]
                match_type = match_type.strip(")")
                datestring = re.search(r"Match Date:\s*(\d{4}-\d{2}-\d{2})", embed.description)
                match_date = datestring.group(1) if datestring else None
                # Check if this is the message author removing their own result
                if user.id != message.author.id:
                    return

                # Get match details
                cursor.execute('''
                SELECT id, winner, score, match_week FROM matches
                WHERE team1 = ? AND team2 = ? AND match_type = ? AND match_date = ?
                ''', (team1, team2, match_type, match_date))
                match_data = cursor.fetchone()

                if not match_data:
                    return

                match_id, current_winner, current_score, match_week = match_data

                if current_winner:  # Only proceed if there's a result to remove
                    # Start transaction
                    cursor.execute('BEGIN TRANSACTION')
                    try:
                        # Get all predictions and their awarded points
                        cursor.execute('''
                        SELECT user_id, points 
                        FROM predictions 
                        WHERE match_id = ? AND points > 0
                        ''', (match_id,))
                        awarded_predictions = cursor.fetchall()

                        # Remove points from predictions
                        cursor.execute('''
                        UPDATE predictions 
                        SET points = 0 
                        WHERE match_id = ?
                        ''', (match_id,))

                        # Remove points from leaderboard
                        for user_id, points in awarded_predictions:
                            cursor.execute('''
                            UPDATE leaderboard 
                            SET weekly_points = weekly_points - ? 
                            WHERE user_id = ? AND match_week = ?
                            ''', (points, user_id, match_week))

                        # Clear the match result
                        cursor.execute('''
                        UPDATE matches 
                        SET winner = NULL, score = NULL 
                        WHERE id = ?
                        ''', (match_id,))

                        cursor.execute('COMMIT')
                        await message.channel.send(f"Result for {team1} vs {team2} has been cleared and points have been removed.")
                        await update_leaderboard()

                    except Exception as e:
                        cursor.execute('ROLLBACK')
                        await message.channel.send(f"Error removing match result: {e}")
                        return

            except Exception as e:
                await message.channel.send(f"Error processing result removal: {e}")
    except Exception as e:
        print(f"Error handling raw reaction removal: {e}")
    

@bot.command()
@commands.check(is_mod_channel)
async def reset_stage(ctx, stage: int):
    """
    Reset leaderboard entries and predictions for a specific tournament stage.
    Args:
        stage: Tournament stage ('G' for Groups, 'SF' for Semi-Finals, 'F' for Finals)
    """
    try:
        # Validate stage input
        if stage not in TOURNAMENT_STAGES:
            await ctx.send("❌ Invalid stage. Use 'G' for Groups, 'SF' for Semi-Finals, or 'F' for Finals.")
            return

        # Start transaction
        cursor.execute('BEGIN TRANSACTION')
        try:
            # Get matches from this stage
            cursor.execute('''
            SELECT id FROM matches 
            WHERE match_week = ?
            ''', (stage,))
            match_ids = [row[0] for row in cursor.fetchall()]

            if not match_ids:
                await ctx.send(f"No matches found for stage {TOURNAMENT_STAGES[stage][0]}.")
                return

            # Delete leaderboard entries for this stage
            cursor.execute('''
            DELETE FROM leaderboard 
            WHERE match_week = ?
            ''', (stage,))

            cursor.execute('COMMIT')
            await ctx.send(f"✅ Successfully reset all entries for {TOURNAMENT_STAGES[stage][0]}.")
            await update_leaderboard()

        except Exception as e:
            cursor.execute('ROLLBACK')
            await ctx.send(f"❌ Error during reset: {e}")

    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def clear_results(ctx, stage: int):
    """
    Removes all winners and scorelines from matches in a specific tournament stage.
    Args:
        stage: Tournament stage ('G' for Groups, 'SF' for Semi-Finals, 'F' for Finals)
    """
    try:
        # Validate stage input
        if stage not in TOURNAMENT_STAGES:
            await ctx.send("❌ Invalid stage. Use 'G' for Groups, 'SF' for Semi-Finals, or 'F' for Finals.")
            return

        # Start transaction
        cursor.execute('BEGIN TRANSACTION')
        try:
            # Get all matches with results from this stage
            cursor.execute('''
            SELECT id, team1, team2, winner, score 
            FROM matches 
            WHERE match_week = ? AND winner IS NOT NULL
            ''', (stage,))
            matches_with_results = cursor.fetchall()

            if not matches_with_results:
                await ctx.send(f"No results found for {TOURNAMENT_STAGES[stage][0]}.")
                return

            # Remove points from predictions and leaderboard
            for match_id, team1, team2, winner, score in matches_with_results:
                # Get predictions that earned points
                cursor.execute('''
                SELECT user_id, points FROM predictions 
                WHERE match_id = ? AND points > 0
                ''', (match_id,))
                awarded_predictions = cursor.fetchall()

                # Reset prediction points
                cursor.execute('UPDATE predictions SET points = 0 WHERE match_id = ?', (match_id,))

                # Remove points from leaderboard
                for user_id, points in awarded_predictions:
                    cursor.execute('''
                    UPDATE leaderboard 
                    SET weekly_points = weekly_points - ? 
                    WHERE user_id = ? AND match_week = ?
                    ''', (points, user_id, stage))

            # Clear all results from this stage
            cursor.execute('''
            UPDATE matches 
            SET winner = NULL, score = NULL 
            WHERE match_week = ?
            ''', (stage,))

            cursor.execute('COMMIT')
            await ctx.send(f"✅ Successfully cleared all results from {TOURNAMENT_STAGES[stage][0]}.")
            await update_leaderboard()

        except Exception as e:
            cursor.execute('ROLLBACK')
            await ctx.send(f"❌ Error during result clearing: {e}")

    except Exception as e:
        await ctx.send(f"❌ Error: {e}")


@bot.command()
@commands.check(is_mod_channel)
async def matches(ctx):
    """
    Display all matches currently in the database.
    """
    cursor.execute('''
    SELECT id, match_date, match_type, team1, team2, poll_created, winner, score
    FROM matches
    ORDER BY match_date
    ''')
    matches = cursor.fetchall()

    if not matches:
        await ctx.send("No matches found in the database!")
        return

    matches_message = "**Current Matches in Database**\n"
    for match in matches:
        match_id, match_date, match_type, team1, team2, poll_created, winner, score = match
        poll_status = "✅" if poll_created else "❌"
        result = f"Winner: {winner}, Score: {score}" if winner else "Result: Not recorded"
        matches_message += (
            f"**Match ID:** {match_id}\n"
            f"**Date:** {match_date}\n"
            f"**Type:** {match_type.upper()}\n"
            f"**Teams:** {team1} vs {team2}\n"
            f"**Poll Created:** {poll_status}\n"
            f"{result}\n\n"
        )

    await ctx.send(matches_message)

@bot.command()
async def predictions(ctx, match_week: int = None):
    """
    Shows a user's predictions for a specific match week.
    If no match_week is provided, it defaults to the latest match week the user has predicted for.
    """
    try:
        user_id = ctx.author.id
        bot_channel_id = 1346615855408091180  # Replace with your bot channel ID
        bot_channel = bot.get_channel(bot_channel_id)

        if isinstance(match_week, str):
            try:
                match_week = int(match_week)
            except ValueError:
                await bot_channel.send("Please provide a valid week number (e.g., 1, 2, 3)")
                return

        # If no match_week is provided, get the latest match week the user has predicted for
        if match_week is None:
            cursor.execute('''
                SELECT DISTINCT match_week
                FROM predictions
                WHERE user_id = ?
                ORDER BY match_week DESC
                LIMIT 1
            ''', (user_id,))
            latest_week = cursor.fetchone()

            if not latest_week:
                await bot_channel.send("You haven't made any predictions yet.")
                return

            match_week = latest_week[0]  # Set match_week to the latest one

        # Fetch match predictions for the given match week
        cursor.execute('''
            SELECT matches.match_date, matches.team1, matches.team2, matches.match_type, 
                   predictions.pred_winner, predictions.pred_score, predictions.points
            FROM matches
            LEFT JOIN predictions 
                ON matches.id = predictions.match_id AND predictions.user_id = ?
            WHERE matches.match_week = ?
            ORDER BY matches.match_date, matches.id
        ''', (user_id, match_week))
        match_predictions = cursor.fetchall()

        # Fetch bonus question predictions for the given match week
        cursor.execute('''
            SELECT bonus_questions.date, bonus_questions.question, bonus_answers.answer, bonus_answers.points
            FROM bonus_questions
            LEFT JOIN bonus_answers
                ON bonus_questions.id = bonus_answers.question_id AND bonus_answers.user_id = ?
            WHERE bonus_questions.match_week = ?
            ORDER BY bonus_questions.date, bonus_questions.id
        ''', (user_id, match_week))
        bonus_predictions = cursor.fetchall()

        # If no predictions are found
        if not match_predictions and not bonus_predictions:
            await bot_channel.send(f"No predictions found for match week {match_week}.")
            return

        # Prepare the embed
        embed = discord.Embed(
            title=f"Predictions for {TOURNAMENT_STAGES[match_week][0]}",
            description=f"{ctx.author.mention}, Here are your predictions for the selected match week.",
            color=discord.Color.blue()
        )

        # Add match predictions
        if match_predictions:
            for match_date, team1, team2, match_type, pred_winner, pred_score, points in match_predictions:
                if pred_winner:
                    prediction_text = f"{pred_winner} {pred_score} (Points: {points if points else 0})"
                else:
                    prediction_text = "No prediction made."

                embed.add_field(
                    name=f"{team1} vs {team2} ({match_type}) - {match_date}",
                    value=prediction_text,
                    inline=False
                )

        # Add bonus question predictions
        if bonus_predictions:
            for date, question, answer, points in bonus_predictions:
                if answer:
                    answer_text = f"{json.loads(answer)} (Points: {points if points else 0})"
                else:
                    answer_text = "No response given."

                embed.add_field(
                    name=f"{question} - {date}",
                    value=answer_text,
                    inline=False
                )

        await bot_channel.send(embed=embed)

    except Exception as e:
        await bot_channel.send(f"An error occurred while fetching your predictions: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def reset_leaderboard(ctx):
    """
    Reset the leaderboard and clear all points.
    """
    # Clear leaderboard data
    cursor.execute('DELETE FROM leaderboard')
    conn.commit()

    # Reset points in predictions table
    cursor.execute('DELETE FROM predictions')
    conn.commit()

    cursor.execute('DELETE FROM bonus_answers')
    conn.commit()

    await ctx.send("Leaderboard has been reset, and all points have been cleared!")

@bot.command()
@commands.check(is_mod_channel)
async def voting_summary(ctx, match_date: str):
    """
    Display voting statistics for matches and bonus questions on a specific date.
    Args:
        match_date: Date of the matches in DD-MM format.
    """
    try:
        # Convert provided date to match format (YYYY-MM-DD)
        match_date_obj = datetime.strptime(match_date, "%d-%m")      
        current_year = datetime.now().year
        match_date_with_year = match_date_obj.replace(year=current_year).strftime("%Y-%m-%d")

        summary_message = f"**Voting Summary for {match_date_with_year}**\n"

        # ---- MATCH VOTING SUMMARY ----
        cursor.execute('''
        SELECT id, team1, team2, match_type
        FROM matches
        WHERE match_date = ?
        ''', (match_date_with_year,))
        matches = cursor.fetchall()

        if matches:
            for match_id, team1, team2, match_type in matches:
                cursor.execute('''
                SELECT pred_winner, pred_score, COUNT(*) AS votes
                FROM predictions
                WHERE match_id = ?
                GROUP BY pred_winner, pred_score
                ORDER BY votes DESC
                ''', (match_id,))
                vote_data = cursor.fetchall()

                # Append match summary
                summary_message += f"\n**Match:** {team1} vs {team2} ({match_type.upper()})\n"
                if vote_data:
                    for pred_winner, pred_score, votes in vote_data:
                        summary_message += f" - {pred_winner} {pred_score}: {votes} vote(s)\n"
                else:
                    summary_message += "   No votes recorded for this match.\n"
        else:
            summary_message += "\nNo matches found for this date.\n"

        # ---- BONUS QUESTION VOTING SUMMARY ----
        cursor.execute('''
        SELECT id, question, options
        FROM bonus_questions
        WHERE date = ?
        ''', (match_date_with_year,))
        bonus_questions = cursor.fetchall()

        if bonus_questions:
            for question_id, question, options in bonus_questions:
                # Count votes for each bonus option
                cursor.execute('''
                SELECT answer, COUNT(*) AS votes
                FROM bonus_answers
                WHERE question_id = ?
                GROUP BY answer
                ORDER BY votes DESC
                ''', (question_id,))
                bonus_vote_data = cursor.fetchall()

                summary_message += f"\n **Bonus Question:** {question}\n"
                if bonus_vote_data:
                    option_votes = {}
                    for answer_json, votes in bonus_vote_data:
                        if answer_json:
                            answers = json.loads(answer_json)
                            # Count each selected option
                            for answer in answers:
                                option_votes[answer] = option_votes.get(answer, 0) + votes

                # Sort and display votes
                    for option, vote_count in sorted(option_votes.items(), key=lambda x: x[1], reverse=True):
                        summary_message += f" - {option}: {vote_count} vote(s)\n"
                else:
                    summary_message += "   No responses recorded for this question.\n"
            else:
                summary_message += "\nNo bonus questions found for this date.\n"

        await ctx.send(summary_message)

    except ValueError:
        await ctx.send("Invalid date format! Please use DD-MM.")



@bot.command()
@commands.check(is_mod_channel)
async def delete_match(ctx, team1: str, team2: str, match_type: str, match_date: str):
    """
    Deletes a match (think UB permutations that don't happen) and all associated votes
    """
    try:
        match_date = datetime.strptime(match_date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = match_date.replace(year=current_year).strftime("%Y-%m-%d")
        
        cursor.execute('DELETE FROM matches WHERE team1 = ? AND team2 = ? AND match_type = ? AND match_date = ?', (team1, team2, match_type, match_date_with_year))
        conn.commit()
        await ctx.send(f"Match has been deleted.")
    except ValueError:
        await ctx.send("Invalid date format. Please use DD-MM.")
    except Exception as e:
        await ctx.send(f"Error deleting match: {e}")

async def delete_polls(match_date: str):
    """
    Deletes all polls associated with the specified match_date.
    Args:
        match_date: The match date in dd-mm format.
    """
    match_date = datetime.strptime(match_date, "%d-%m")      
    current_year = datetime.now().year
    match_date_with_year = match_date.replace(year=current_year).strftime("%Y-%m-%d")
    print(match_date_with_year)
    try:
        # Fetch the channel IDs where the polls are located
        poll_channel_id = 1346615134885253181  # Replace with actual channel IDs        
        poll_channel = bot.get_channel(poll_channel_id)
        if poll_channel is None:
            print(f"Channel with ID {poll_channel_id} not found.")
            return

        # Fetch all messages from the channel
        async for message in poll_channel.history(limit=200):
            if message.author == bot.user and message.embeds:
                embed = message.embeds[0]
                if embed.description:
                    # Look for date in the first line of description
                    date_line = embed.description.split('\n')[0]
                    if f"Match Date: {match_date_with_year}" in date_line:
                        await message.delete()

        print(f"All polls for {match_date} have been successfully deleted.")

    except Exception as e:
        print(f"Error deleting polls for {match_date}: {e}")


@bot.command()
@commands.check(is_mod_channel)
async def schedule_poll_deletion(ctx, match_date: str):
    """
    Schedules the deletion of polls for the given match_date at 5 PM UK time.
    match_date format: dd-mm
    """
    try:
        # Convert match_date to datetime
        match_date_dt = datetime.strptime(match_date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = match_date_dt.replace(year=current_year)

        deletion_time_utc = uk_tz.localize(
            datetime.combine(match_date_with_year.date(), datetime.min.time()) + timedelta(hours=16)
        ).astimezone(pytz.utc)  # Convert to UTC for the scheduler

        # Schedule task
        scheduler.add_job(delete_polls, "date", run_date=deletion_time_utc, args=[match_date])
        await ctx.send(f"Poll deletion for {match_date} scheduled at 4 PM UK time.")

    except Exception as e:
        await ctx.send(f"Error scheduling poll deletion: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def announce(ctx):
    """Takes the last message from the source channel, posts it in the announcement channel, and closes the poll channel."""
    try:
        poll_channel_id = 1346615134885253181  # Replace with actual channel IDs        
        poll_channel = bot.get_channel(poll_channel_id)
        source_channel_id = 1346615886848593985
        source_channel = bot.get_channel(source_channel_id)
        announcement_channel_id = 800704760284971058
        announcement_channel = bot.get_channel(announcement_channel_id)
        announcement_channel2 = bot.get_channel(381820768310263818)  # Add your second channel ID here
        # Fetch the last message from the source channel
        async for message in source_channel.history(limit=1):
            last_message = message
            break  # Get the first (latest) message
        else:
            await ctx.send("⚠ No messages found in the source channel.")
            return

        # Send the last message to the announcement channel
        content = last_message.content
        # Replace role mentions with plain text version
        ping_less_content = re.sub(r'<@&(\d+)>', 
            lambda m: f'@{ctx.guild.get_role(int(m.group(1))).name}' 
            if ctx.guild.get_role(int(m.group(1))) else '@role', 
            content)

        # Send ping-less version to announcement channel
        await announcement_channel.send(ping_less_content)
        
        # Send original version with ping to ping channel
        await announcement_channel2.send(content)

        overwrite = discord.PermissionOverwrite()
        overwrite.send_messages = False
        overwrite.read_messages = True
        overwrite.add_reactions = True
        overwrite.view_channel = True
        # Close (make poll channel private)
        await poll_channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(f"Announcement sent in {announcement_channel.mention}, and {poll_channel.mention} is now **open**!")

    except Exception as e:
        await ctx.send(f"Error: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def close_channel(ctx):
    """Makes the poll channel private."""
    try:
        poll_channel_id = 1346615134885253181  # Replace with actual channel IDs        
        poll_channel = bot.get_channel(poll_channel_id)
        # Make the channel private
        overwrite = discord.PermissionOverwrite()
        overwrite.send_messages = False
        overwrite.read_messages = True
        overwrite.add_reactions = True
        overwrite.view_channel = False

        await poll_channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(f"{poll_channel.mention} is now **private**.")

    except Exception as e:
        await ctx.send(f"Error: {e}")


@bot.command()
async def predictions_table(ctx, match_date: str):
    """Creates an image showing all predictions for matches on a given date."""
    try:
        bot_channel_id = 1346615855408091180
        bot_channel = bot.get_channel(bot_channel_id)
        match_date_obj = datetime.strptime(match_date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = match_date_obj.replace(year=current_year).strftime("%Y-%m-%d")

        # Get all users, not just those who predicted
        cursor.execute('''
        WITH UserPoints AS (
            SELECT u.user_id, u.username, COALESCE(SUM(l.weekly_points), 0) as total_points,
                GROUP_CONCAT(l.match_week || ':' || l.weekly_points) as weekly_scores
            FROM users u
            LEFT JOIN leaderboard l ON u.user_id = l.user_id
            GROUP BY u.user_id, u.username
        )
        SELECT username, total_points, weekly_scores
        FROM UserPoints
        ORDER BY total_points DESC, weekly_scores DESC
        ''')
        users = cursor.fetchall()

        # Get matches for the date
        cursor.execute('''
        SELECT id, team1, team2, match_type
        FROM matches
        WHERE match_date = ?
        ORDER BY id
        ''', (match_date_with_year,))
        matches = cursor.fetchall()

        # Image dimensions
        width = 200 + (len(matches) * 150)
        header_height = 60
        row_height = 30
        column_width = 150
        username_width = 150
        points_width = 100
        grid_color = 'gray'
        line_thickness = 2
        padding = 10

        total_rows = len(users)
        height = header_height + (row_height * (total_rows + 1)) + padding

        # Create image
        img = Image.new('RGB', (width, height), color='white')
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype("arial.ttf", 36)
        except:
            font = ImageFont.load_default()

        # Draw header
        draw.rectangle([0, 0, width, header_height], fill='lightblue')
        draw.text((padding, 20), f"Predictions for {match_date}", font=font, fill='black')

        # Draw grid
        # Vertical lines
        x = username_width
        draw.line([(x, header_height), (x, height)], fill=grid_color, width=line_thickness)
        x += points_width
        draw.line([(x, header_height), (x, height)], fill=grid_color, width=line_thickness)
        for i in range(len(matches)):
            x += column_width
            draw.line([(x, header_height), (x, height)], fill=grid_color, width=line_thickness)

        # Horizontal lines
        for i in range(total_rows + 2):
            y = header_height + (i * row_height)
            draw.line([(0, y), (width, y)], fill=grid_color, width=line_thickness)

        # Draw column headers
        y = header_height + padding
        draw.text((padding, y), "Username", font=font, fill='black')
        draw.text((username_width + padding, y), "Points", font=font, fill='black')
        x = username_width + points_width + padding
        for match in matches:
            draw.text((x, y), f"{match[1]} vs {match[2]}", font=font, fill='black')
            x += column_width

        # Colors for top positions
        position_colors = {
            0: '#FFD700',  # Gold
            1: '#C0C0C0',  # Silver
            2: '#CD7F32'   # Bronze
        }

        # Get top 3 scores to handle ties
        scores = sorted(set(points for _, points, _ in users), reverse=True)[:3]
        
        # Draw predictions
        y = header_height + row_height + padding
        for i, (username, total_points, weekly_scores) in enumerate(users):
            # Determine background color based on position
            if total_points in scores[:3]:
                position = scores.index(total_points)
                bg_color = position_colors[position]
                draw.rectangle([0, y-padding, width, y+row_height-padding], fill=bg_color)

            draw.text((padding, y), username, font=font, fill='black')
            draw.text((username_width + padding, y), str(total_points), font=font, fill='black')
            
            x = username_width + points_width + padding
            for match in matches:
                cursor.execute('''
                SELECT pred_winner, pred_score
                FROM predictions
                JOIN users ON predictions.user_id = users.user_id
                WHERE match_id = ? AND users.username = ?
                ''', (match[0], username))
                pred = cursor.fetchone()
                pred_text = f"{pred[0]} {pred[1]}" if pred else "No prediction"
                draw.text((x, y), pred_text, font=font, fill='black')
                x += column_width
            y += row_height
        
        img = img.resize((width, height), Image.LANCZOS)

        # Save and send image
        with io.BytesIO() as image_binary:
            img.save(image_binary, 'PNG', quality=95, optimize=True)
            image_binary.seek(0)
            await bot_channel.send(file=discord.File(fp=image_binary, filename='predictions.png'))

    except Exception as e:
        await ctx.send(f"Error creating predictions image: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def add_prediction(ctx, username: str, match_date: str, team1: str, team2: str, pred_winner: str, pred_score: str):
    """
    Manually add a prediction for a user.
    Usage: !add_prediction <username> <DD-MM> <team1> <team2> <predicted_winner> <predicted_score>
    Example: !add_prediction "Player Name" 11-03 "Team1" "Team2" "Team1" "2-0"
    """
    try:
        # Find user_id from username
        cursor.execute('''
        SELECT user_id FROM users 
        WHERE username = ?
        ''', (username,))
        user_row = cursor.fetchone()

        if not user_row:
            await ctx.send(f"No user found with username: {username}")
            return

        user_id = user_row[0]

        # Convert date format
        match_date_obj = datetime.strptime(match_date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = match_date_obj.replace(year=current_year).strftime("%Y-%m-%d")

        # Find the match
        cursor.execute('''
        SELECT id, match_week FROM matches 
        WHERE team1 = ? AND team2 = ? AND match_date = ?
        ''', (team1, team2, match_date_with_year))
        match_data = cursor.fetchone()

        if not match_data:
            await ctx.send(f"No match found for {team1} vs {team2} on {match_date}")
            return

        match_id, match_week = match_data

        # Add the prediction
        cursor.execute('''
        INSERT INTO predictions (user_id, match_id, match_week, pred_winner, pred_score)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, match_id) DO UPDATE SET
            pred_winner = excluded.pred_winner,
            pred_score = excluded.pred_score
        ''', (user_id, match_id, int(match_week), pred_winner, pred_score))

        conn.commit()
        await ctx.send(f"Added prediction for {username}: {pred_winner} {pred_score} in {team1} vs {team2}")

    except ValueError:
        await ctx.send("Invalid date format. Please use DD-MM.")
    except Exception as e:
        await ctx.send(f"Error adding prediction: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def update_board(ctx):
    """
    Manually triggers a leaderboard update.
    """
    try:
        await update_leaderboard()
        await ctx.send("Leaderboard has been manually updated.")
    except Exception as e:
        await ctx.send(f"Error updating leaderboard: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def recalculate_weeks(ctx):
    """Recalculates weekly points for all users based on stored predictions and bonus answers."""
    try:
        cursor.execute('BEGIN TRANSACTION')
        
        # Clear existing leaderboard
        cursor.execute('DELETE FROM leaderboard')
        
        # Recalculate match prediction points
        cursor.execute('''
        INSERT INTO leaderboard (user_id, match_week, weekly_points)
        SELECT user_id, match_week, SUM(points) as total_points
        FROM predictions
        WHERE points > 0
        GROUP BY user_id, match_week
        ''')
        
        # Add bonus question points
        cursor.execute('''
        INSERT INTO leaderboard (user_id, match_week, weekly_points)
        SELECT user_id, match_week, SUM(points) as total_points
        FROM bonus_answers
        WHERE points > 0
        GROUP BY user_id, match_week
        ON CONFLICT(user_id, match_week) 
        DO UPDATE SET weekly_points = leaderboard.weekly_points + excluded.weekly_points
        ''')
        
        cursor.execute('COMMIT')
        await ctx.send("✅ Weekly points recalculated successfully!")
        await update_leaderboard()
        
    except Exception as e:
        cursor.execute('ROLLBACK')
        await ctx.send(f"❌ Error recalculating points: {e}")


# Run bot
bot.run(TOKEN)