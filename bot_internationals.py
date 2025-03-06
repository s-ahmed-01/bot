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
    match_week TEXT NOT NULL,
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
    match_week TEXT NOT NULL,
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
    points INTEGER NOT NULL
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
    match_week TEXT,
    weekly_points INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, match_week)
)
''')
conn.commit()

TOURNAMENT_STAGES = {
    'G': ('Group Stage', 1),
    'SF': ('Semi-Finals', 2),
    'F': ('Finals', 3)
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
    try:
        leaderboard_channel_id = 1346615199544905730
        leaderboard_channel = bot.get_channel(leaderboard_channel_id)
        if not leaderboard_channel:
            print("Error: Leaderboard channel not found.")
            return

        # Fetch leaderboard data
        cursor.execute('''
            SELECT user_id, match_week, weekly_points
            FROM leaderboard
            ORDER BY CASE match_week 
                WHEN 'G' THEN 1
                WHEN 'SF' THEN 2
                WHEN 'F' THEN 3
            END ASC
        ''')
        leaderboard_data = cursor.fetchall()

        if not leaderboard_data:
            leaderboard_message = "**🏆 Leaderboard 🏆**\n\nNo points have been awarded yet!"
        else:
            leaderboard_dict = {}
            user_id_list = set()

            # Define stage order for sorting
            stage_order = {'G': 1, 'SF': 2, 'F': 3}

            # Organize leaderboard data by user_id
            for user_id, match_week, weekly_points in leaderboard_data:
                if user_id not in leaderboard_dict:
                    leaderboard_dict[user_id] = {"stages": {}, "total": 0}
                if match_week not in leaderboard_dict[user_id]["stages"]:
                    leaderboard_dict[user_id]["stages"][match_week] = 0
                leaderboard_dict[user_id]["stages"][match_week] = weekly_points
                leaderboard_dict[user_id]["total"] += weekly_points

            # Fetch usernames
            cursor.execute(f'''
                SELECT user_id, username FROM users
                WHERE user_id IN ({",".join(["?"] * len(user_id_list))})
            ''', tuple(user_id_list))
            user_data = dict(cursor.fetchall())

            def tie_breaker(user_data, latest_stage):
                """
                Break ties by comparing scores from previous stages.
                """
                def compare_users(user1, user2, stage):
                    if stage not in ['G', 'SF', 'F']:
                        return 0  # No more stages to compare

                    score1 = user_data[user1]["stages"].get(stage, 0)
                    score2 = user_data[user2]["stages"].get(stage, 0)

                    print(f"Comparing users {user1} and {user2} for stage {stage}: score1={score1}, score2={score2}")

                    if score1 != score2:
                        return score2 - score1  # Higher score first

                    # Get previous stage
                    stages = ['G', 'SF', 'F']
                    current_index = stages.index(stage)
                    if current_index > 0:
                        return compare_users(user1, user2, stages[current_index - 1])
                    return 0

                def sort_key(user):
                    return (
                        user_data[user]["total"],
                        user_data[user]["stages"].get(latest_stage, 0)
                    )

                sorted_users = sorted(user_data.keys(), key=sort_key, reverse=True)
                print(f"sorted_users before tie-breaking: {sorted_users}")

                # Handle ties
                i = 0
                while i < len(sorted_users) - 1:
                    j = i
                    while j < len(sorted_users) - 1 and sort_key(sorted_users[j]) == sort_key(sorted_users[j + 1]):
                        j += 1
                    if j > i:
                        tied_users = sorted_users[i:j + 1]
                        print(f"Tie detected between users: {tied_users}")
                        try:
                            tied_users.sort(
                                key=functools.cmp_to_key(
                                    lambda x, y: compare_users(x, y, latest_stage)
                                ),
                                reverse=True
                            )
                            sorted_users[i:j + 1] = tied_users
                        except Exception as e:
                            print(f"Error during tie-breaking: {e}")
                    i = j + 1

                return sorted_users

            # Get the current stage
            cursor.execute('''
                SELECT match_week 
                FROM leaderboard 
                ORDER BY CASE match_week
                    WHEN 'G' THEN 1
                    WHEN 'SF' THEN 2
                    WHEN 'F' THEN 3
                END DESC
                LIMIT 1
            ''')
            latest_stage = cursor.fetchone()[0] or 'G'  # Default to Group stage if none found
            
            sorted_users = tie_breaker(leaderboard_dict, latest_stage)
            leaderboard_message = "**🏆 Leaderboard 🏆**\n\n"
            
            for rank, user_id in enumerate(sorted_users, start=1):
                data = leaderboard_dict[user_id]
                username = user_data.get(user_id)
                if not username:
                    try:
                        user = await bot.fetch_user(user_id)
                        username = user.name
                    except:
                        username = f"Unknown ({user_id})"

                stage_scores = " | ".join(
                    f"{stage}: {points}" for stage, points in 
                    sorted(data["stages"].items(), key=lambda x: stage_order[x[0]])
                )
                leaderboard_message += f"{rank}. **{username}** - {stage_scores} | **Total: {data['total']}**\n"

        # Update or send leaderboard message
        async for message in leaderboard_channel.history(limit=5):
            if message.author == bot.user:
                await message.edit(content=leaderboard_message)
                return

        await leaderboard_channel.send(leaderboard_message)

    except Exception as e:
        print(f"Error updating leaderboard: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def schedule(ctx, match_date: str, match_type: str, match_week: str, team1: str, team2: str, winner_points: int = 0, scoreline_points:int = 0):
    """
    Schedule a match with a specific date.
    Args:
        match_date: Date of the match in DD-MM format.
        match_type: Type of match ('bo1', 'bo3', or 'bo5').
        match_week: Stage of the tournament ('G', 'SF', 'F').
        team1: Name of the first team.
        team2: Name of the second team.
        winner/scoreline points: optional, will default to appropriate points if no value given
    """
    try:
        # Validate and parse match_date
        parsed_date = datetime.strptime(match_date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = parsed_date.replace(year=current_year)

        # Insert into the database with the full date and calculated match_week
        cursor.execute('''
        INSERT INTO matches (match_date, match_type, team1, team2, match_week, winner_points, scoreline_points)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (match_date_with_year.strftime("%Y-%m-%d"), match_type.upper(), team1, team2, match_week, winner_points, scoreline_points))
        conn.commit()

        await ctx.send(f"✅ Match scheduled: {team1} vs {team2} on {match_date_with_year.strftime('%d-%m')} (Week {match_week})")

    except ValueError:
        await ctx.send("❌ Invalid date format. Please use DD-MM.")

@bot.command()
@commands.check(is_mod_channel)
async def add_bonus_question(ctx, date: str, match_week: str, question: str, description: str, options: str, required_answers: int = 1, points: int = 1):
    """
    Adds a bonus question to the database.
    Requires:
    Date in DD-MM format, the question (w/ quotation marks), any description (w/ quotation marks), options (list surrounded by quotation marks), required answers (will default to 1 if no value), points (default value 1)
    """
    try:
        parsed_date = datetime.strptime(date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = parsed_date.replace(year=current_year)

        cursor.execute('''
        INSERT INTO bonus_questions (date, question, description, options, required_answers, points, match_week)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (match_date_with_year.strftime("%Y-%m-%d"), question, description, options, required_answers, points, match_week))
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
        SELECT id, date, question, description, options, points
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
                reactions = ['1️⃣', '2️⃣']
            elif match_type == 'BO3':
                options = [f"{team1} 2-0", f"{team1} 2-1", f"{team2} 2-1", f"{team2} 2-0"]
                reactions = ['1️⃣', '2️⃣', '3️⃣', '4️⃣']
            elif match_type == 'BO5':
                options = [
                    f"{team1} 3-0", f"{team1} 3-1", f"{team1} 3-2",
                    f"{team2} 3-2", f"{team2} 3-1", f"{team2} 3-0"
                ]
                reactions = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣']

            # Create prediction and result polls for the match
            await create_match_poll(poll_channel, admin_channel, match_id, match_date, team1, team2, match_type, options, reactions, winner_points, scoreline_points)

        # --- Create polls for bonus questions ---
        for question in bonus_questions:
            question_id, match_date, question_text, description, options, point_value = question

            if isinstance(match_date, str):
                match_date = datetime.strptime(match_date, "%Y-%m-%d").date()

            option_split = [option.strip() for option in options.split(",")]
            reactions = [f"{i + 1}️⃣" for i in range(len(option_split))]

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
    for i, option in enumerate(options, start=1):
        prediction_embed.add_field(name=f"Option {i}", value=option, inline=False)

    prediction_message = await prediction_channel.send(embed=prediction_embed)
    for reaction in reactions:
        await prediction_message.add_reaction(reaction)

    # Create result poll
    result_embed = discord.Embed(
        title=f"Result Poll: {team1} vs {team2} ({match_type})",
        description=f"Match Date: {match_date}\nReact with the correct result!",
        color=discord.Color.green()
    )
    for i, option in enumerate(options, start=1):
        result_embed.add_field(name=f"Option {i}", value=option, inline=False)

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
        description=description + (f"Points: {points}"),
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
async def on_reaction_add(reaction, user):
    if user.id == bot.user.id:
        return  # Ignore reactions from this bot only

    message = reaction.message

    # Ensure the message has an embed
    if not message.embeds:
        return

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
                ORDER BY CASE match_week
                    WHEN 'G' THEN 1
                    WHEN 'SF' THEN 2
                    WHEN 'F' THEN 3
                END DESC
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
            reactions = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣'][:len(options)]

            if str(reaction.emoji) not in reactions:
                await message.channel.send("Invalid reaction. Please select a valid option.")
                return

            selected_index = reactions.index(str(reaction.emoji))
            prediction = options[selected_index]
            pred_winner, pred_score = prediction.split(" ", 1)

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

            await message.channel.send(f"{user.name} your prediction has been logged: {pred_winner} with score {pred_score}.")

        elif poll_type == "result_poll":
            # Handle result poll
            options = [field.value for field in embed.fields]
            reactions = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣'][:len(options)]

            if str(reaction.emoji) not in reactions:
                await message.channel.send("Invalid reaction. Please select a valid option.")
                return

            selected_index = reactions.index(str(reaction.emoji))
            result = options[selected_index]
            winner, score = result.split(" ", 1)

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
        SELECT id, match_week, options, required_answers, points FROM bonus_questions
        WHERE question = ?
        ORDER BY id DESC LIMIT 1
        ''', (question_text,))
        question_row = cursor.fetchone()

        if not question_row:
            await message.channel.send(f"Error: No bonus question found for '{question_text}'.")
            return

        question_id, week, options, required_answers, points_value = question_row
        option_split = [option.strip() for option in options.split(",")]
        reactions = [f"{i + 1}️⃣" for i in range(len(option_split))]

        if str(reaction.emoji) not in reactions:
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
            missed_weeks = list(range(latest_week + 1, match_row[1]))
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
                    selected_index = reactions.index(str(reaction.emoji))
                except ValueError:
                    await message.channel.send("Error: Invalid reaction. Please select a valid option.")
                    return

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
                    selected_index = reactions.index(str(reaction.emoji))
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
                    await message.channel.send("You have already selected an answer. Please remove one first if you wish to change your answer.")
                    return
                # Get the latest match week the user has participated in
                
            except ValueError:
                # Handle cases where the emoji is not in the reactions list
                await message.channel.send("Error: Reaction not recognized. Please react with a valid emoji.")
                return


        elif poll_type == "bonus_result":
            cursor.execute('''
            SELECT correct_answer FROM bonus_questions
            WHERE question = ?
            ORDER BY id DESC LIMIT 1
            ''', (question_text,))
            answer_row = cursor.fetchone()
            
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

            user_input = dict(zip(reactions, option_split)).get(str(reaction.emoji), None)

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

            if str(reaction.emoji) == "✅":  # Change this emoji to whatever you prefer
                await message.channel.send(f"✅ Correct answer selection finalized! Checking responses...")

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
                    user_selections = set(json.loads(user_selections_json))  # Convert user answers

                    if len(correct_answers) > required_answers:
                        # If only one answer is expected, allow any one correct answer
                        if user_selections.issubset(correct_answers):  # Intersection (checks if at least one is correct)
                            points_awarded = points_value  # Full points for selecting at least one
                        else:
                            points_awarded = 0  # No points if none were correct
                    else:
                        # Default behavior: Require an exact match of all correct answers
                        points_awarded = points_value if user_selections == correct_answers else 0

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
                    ''', (user_id, match_week, points, points))
                    conn.commit()

                    if points_awarded > 0:
                        awarded_users.append(user_id)

                # --- **Send Final Result Message** ---
                correct_answer_text = ", ".join(correct_answers)
                if awarded_users:
                    awarded_mentions = ", ".join([f"<@{user_id}>" for user_id in awarded_users])
                    await message.channel.send(f"✅ Points awarded! The correct answer was: {correct_answer_text}. Users awarded: {awarded_mentions}")
                else:
                    await message.channel.send(f"❌ No users selected the correct answer. The correct answer was: {correct_answer_text}.")

                await update_leaderboard()

@bot.event
async def on_reaction_remove(reaction, user):
    print("hi")
    if user.bot:
        return  # Ignore bot reactions

    message = reaction.message
    if not message.embeds:
        return

    embed = message.embeds[0]
    title = embed.title

    if "Bonus Question" in title:
        question_text = title.split(":")[1].strip()

        cursor.execute('''
        SELECT id, options FROM bonus_questions
        WHERE question = ?
        ''', (question_text,))
        question_row = cursor.fetchone()

        if not question_row:
            return

        question_id, options = question_row
        option_split = [option.strip() for option in options.split(",")]
        reactions = [f"{i + 1}️⃣" for i in range(len(option_split))]

        if str(reaction.emoji) not in reactions:
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
        selected_index = reactions.index(str(reaction.emoji))
        selected_option = option_split[selected_index]

        if selected_option in existing_answers:
            print("i got here :/)")
            existing_answers.remove(selected_option)
            await message.channel.send(f"{selected_option} has been removed from your selection.")
            print(existing_answers)  # Remove selection

        updated_answers = json.dumps(existing_answers)

        # Update the database
        cursor.execute('''
        UPDATE bonus_answers
        SET answer = ?
        WHERE user_id = ? AND question_id = ?
        ''', (updated_answers, user.id, question_id))
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

            # Delete Prediction from Database
            cursor.execute('''
            UPDATE predictions 
            SET pred_winner = NULL, pred_score = NULL
            WHERE match_id = ? AND user_id = ?
            ''', (match_id, user.id))
            conn.commit()


            await message.channel.send(f"{user.mention}, your prediction has been removed.")

        except Exception as e:
            print(f"Error removing match prediction: {e}")
    




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
async def predictions(ctx, match_week: str = None):
    """
    Shows a user's predictions for a specific match week.
    If no match_week is provided, it defaults to the latest match week the user has predicted for.
    """
    try:
        user_id = ctx.author.id
        bot_channel_id = 1346615855408091180  # Replace with your bot channel ID
        bot_channel = bot.get_channel(bot_channel_id)

        # If no match_week is provided, get the latest match week the user has predicted for
        if match_week is None:
            cursor.execute('''
                SELECT DISTINCT match_week
                FROM predictions
                WHERE user_id = ?
                ORDER BY CASE match_week
                    WHEN 'G' THEN 1
                    WHEN 'SF' THEN 2
                    WHEN 'F' THEN 3
                END DESC
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
            title=f"{ctx.author.mention}, your Predictions for {match_week}",
            description="Here are your predictions for the selected match week.",
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
                    name=f"❓ {question} - {date}",
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

        summary_message = f"**📊 Voting Summary for {match_date_with_year}**\n"

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
            summary_message += "\n⚠ No matches found for this date.\n"

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
                    for answer, votes in bonus_vote_data:
                        summary_message += f" - {answer}: {votes} vote(s)\n"
                else:
                    summary_message += "   No responses recorded for this question.\n"
        else:
            summary_message += "\nNo bonus questions found for this date.\n"

        await ctx.send(summary_message)

    except ValueError:
        await ctx.send("❌ Invalid date format! Please use DD-MM.")



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
    match_date_with_year = match_date.replace(year=current_year.strftime("%Y-%m-%d"))
    try:
        # Fetch the channel IDs where the polls are located
        poll_channel_id = 1346615134885253181  # Replace with actual channel IDs        
        poll_channel = bot.get_channel(poll_channel_id)
        if poll_channel is None:
            print(f"Channel with ID {poll_channel_id} not found.")
            return

        # Fetch all messages from the channel
        async for message in poll_channel.history(limit=200):  # Adjust the limit if needed
            if message.author == bot.user and message.embeds:
                embed = message.embeds[0]  # Get the first embed
                if embed.description and match_date_with_year in embed.description:
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
            datetime.combine(match_date_with_year, datetime.min.time()) + timedelta(hours=17)
        ).astimezone(pytz.utc)  # Convert to UTC for the scheduler

        # Schedule task
        scheduler.add_job(delete_polls, "date", run_date=deletion_time_utc, args=[match_date_with_year.strftime("%d-%m")])
        await ctx.send(f"Poll deletion for {match_date} scheduled at 5 PM UK time.")

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


        # Close (make poll channel private)
        await poll_channel.set_permissions(ctx.guild.default_role, view_channel=True)
        await ctx.send(f"📢 Announcement sent in {announcement_channel.mention}, and {poll_channel.mention} is now **open**!")

    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command()
@commands.check(is_mod_channel)
async def close_channel(ctx):
    """Makes the poll channel private."""
    try:
        poll_channel_id = 1346615134885253181  # Replace with actual channel IDs        
        poll_channel = bot.get_channel(poll_channel_id)
        # Make the channel private
        await poll_channel.set_permissions(ctx.guild.default_role, view_channel=False)
        await ctx.send(f"🔒 {poll_channel.mention} is now **private**.")

    except Exception as e:
        await ctx.send(f"❌ Error: {e}")


# Run bot
bot.run(TOKEN)