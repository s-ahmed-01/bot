import asyncio
import discord
from discord.ext import commands
import sqlite3  # Replace with pymongo if you prefer MongoDB
import os
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import pytz
import re
import json

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Bot setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
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
    points INTEGER NOT NULL
);

''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS leaderboard (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    points INTEGER
)
''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL
)
''')
conn.commit()

# Event: Bot ready
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def update_leaderboard():
    """
    Updates the leaderboard message in a dedicated channel.
    """
    try:
        leaderboard_channel_id = 1336468081563930674  # Replace with your actual channel ID
        leaderboard_channel = bot.get_channel(leaderboard_channel_id)

        if not leaderboard_channel:
            print("Error: Leaderboard channel not found.")
            return

        # Fetch leaderboard data: user_id, username, match_week, and total points
        cursor.execute('''
        SELECT p.user_id, u.username, p.match_week, SUM(p.points) 
        FROM predictions p
        JOIN users u ON p.user_id = u.user_id
        GROUP BY p.user_id, p.match_week
        ORDER BY SUM(p.points) DESC, p.match_week ASC
        ''')
        leaderboard_data = cursor.fetchall()

        if not leaderboard_data:
            leaderboard_message = "**🏆 Leaderboard 🏆**\n\nNo points have been awarded yet!"
        else:
            leaderboard_dict = {}
            for user_id, username, match_week, points in leaderboard_data:
                if username not in leaderboard_dict:
                    leaderboard_dict[username] = {"weeks": {}, "total": 0}
                leaderboard_dict[username]["weeks"][match_week] = points
                leaderboard_dict[username]["total"] += points

            # Format the leaderboard message
            leaderboard_message = "**🏆 Leaderboard 🏆**\n\n"
            for rank, (username, data) in enumerate(
                sorted(leaderboard_dict.items(), key=lambda x: x[1]["total"], reverse=True), start=1
            ):
                week_scores = " | ".join(f"Week {week}: {points}" for week, points in sorted(data["weeks"].items()))
                leaderboard_message += f"{rank}. **{username}** - {week_scores} | **Total: {data['total']}**\n"

        # Fetch the most recent leaderboard message
        async for message in leaderboard_channel.history(limit=5):
            if message.author == bot.user:
                await message.edit(content=leaderboard_message)
                return

        # If no previous leaderboard message found, send a new one
        await leaderboard_channel.send(leaderboard_message)

    except Exception as e:
        print(f"Error updating leaderboard: {e}")




@bot.command()
async def schedule(ctx, match_date: str, match_type: str, team1: str, team2: str, winner_points: int = None, scoreline_points:int = None):
    """
    Schedule a match with a specific date.
    Args:
        match_date: Date of the match in DD-MM format.
        match_type: Type of match ('bo1', 'bo3', or 'bo5').
        team1: Name of the first team.
        team2: Name of the second team.
    """
    try:
        # Validate and parse match_date
        parsed_date = datetime.strptime(match_date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = parsed_date.replace(year=current_year)

        # Determine the match week
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
        ''', (match_date_with_year.strftime("%Y-%m-%d"), match_type, team1, team2, match_week, winner_points, scoreline_points))
        conn.commit()

        await ctx.send(f"✅ Match scheduled: {team1} vs {team2} on {match_date_with_year.strftime('%d-%m')} (Week {match_week})")

    except ValueError:
        await ctx.send("❌ Invalid date format. Please use DD-MM.")

@bot.command()
async def add_bonus_question(ctx, date: str, question: str, description: str, options: str, required_answers: int = 1, points: int = None):
    """
    Adds a bonus question to the database.
    """
    try:
        parsed_date = datetime.strptime(date, "%d-%m")
        current_year = datetime.now().year
        match_date_with_year = parsed_date.replace(year=current_year)

        cursor.execute("SELECT date, match_week FROM bonus_questions ORDER BY match_week DESC")
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

        cursor.execute('''
        INSERT INTO bonus_questions (date, question, description, options, required_answers, points, match_week)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (match_date_with_year.strftime("%Y-%m-%d"), question, description, options, required_answers, points, match_week))
        conn.commit()

        await ctx.send(f"Bonus question added for {date}: {question}")
    except Exception as e:
        await ctx.send(f"Error adding bonus question: {e}")


@bot.command()
async def create_polls(ctx):
    """
    Creates prediction polls in Channel A and result polls in Channel B for:
    1. Matches with poll_created = False.
    2. Bonus questions with poll_created = False.
    Adds date headers to split matches and questions by day for better navigation.
    """
    try:
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

        # Define Channel IDs
        prediction_channel_id = 1275834751697027213   # Replace with Channel A ID
        result_channel_id = 1330957256505692325   # Replace with Channel B ID

        # Fetch channels
        prediction_channel = bot.get_channel(prediction_channel_id)
        result_channel = bot.get_channel(result_channel_id)

        if not prediction_channel or not result_channel:
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
                await prediction_channel.send(f"**{formatted_date} Games**")
                await result_channel.send(f"**{formatted_date} Games**")

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
            await create_match_poll(prediction_channel, result_channel, match_id, match_date, team1, team2, match_type, options, reactions, winner_points, scoreline_points)

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
                await prediction_channel.send(f"**{formatted_date} Bonus Questions**")
                await result_channel.send(f"**{formatted_date} Bonus Questions**")

            # Create prediction and result polls for the bonus question
            await create_bonus_poll(prediction_channel, result_channel, question_id, question_text, description, option_split, reactions, point_value)

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
    if user.bot:
        return  # Ignore bot reactions

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


            await message.channel.send(f"{user.mention} your prediction has been logged: {pred_winner} with score {pred_score}.")

        elif poll_type == "result_poll":
            # Handle result poll (log result and award points)
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
            SELECT id, user_id, pred_winner, pred_score
            FROM predictions
            WHERE match_id = ?
            ''', (match_id,))
            predictions = cursor.fetchall()

            for prediction in predictions:
                pred_id, user_id, pred_winner, pred_score = prediction
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

            conn.commit()

            await update_leaderboard()

            await message.channel.send(
                f"Result recorded for match {team1} vs {team2} ({match_type}): {winner} wins with score {score}! Points have been awarded."
            )
    elif poll_type == "bonus_poll" or poll_type == "bonus_result":
        # Locate the question in the database
        question_text = title.split(":")[1].strip()  # Extract question text
        cursor.execute('''
        SELECT id, match_week, options, points, correct_answer FROM bonus_questions
        WHERE question = ?
        ''', (question_text,))
        question_row = cursor.fetchone()

        if not question_row:
            await message.channel.send(f"Error: No bonus question found for '{question_text}'.")
            return

        question_id, week, options, points_value = question_row
        option_split = [option.strip() for option in options.split(",")]
        reactions = [f"{i + 1}️⃣" for i in range(len(option_split))]

        if str(reaction.emoji) not in reactions:
            await message.channel.send("Invalid reaction. Please select a valid option.")
            return

        if poll_type == "bonus_poll":
            # Track the user's selected reactions
            if not hasattr(bot, "user_reactions"):
                bot.user_reactions = {}

            user_selections = bot.user_reactions.get(user_reaction_key, set())

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

                if existing_answer_row and existing_answer_row[0]:
                    existing_answers = json.loads(existing_answer_row[0])
                else:
                    existing_answers = []

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


                await update_leaderboard()

            except ValueError:
                # Handle cases where the emoji is not in the reactions list
                await message.channel.send("Error: Reaction not recognized. Please react with a valid emoji.")
                return


        elif poll_type == "bonus_result":
            if not question_row:
                await message.channel.send(f"Error: No bonus question found for '{question_text}'.")
                return

            if question_row and question_row[3]:
                correct_answers = set(json.loads(question_row[4]))
            else:
                correct_answers = set()

            user_input = dict(zip(reactions, option_split)).get(str(reaction.emoji), None)

            correct_answers.append(user_input)  # Add selection

            correct_answers_json = json.dumps(correct_answers)

            cursor.execute('''
            UPDATE bonus_questions
            SET correct_answer = ?
            WHERE id = ?
            ''', (correct_answers_json, question_id))
            conn.commit()
            await message.channel.send(f"✅ The correct answer for '{question_text}' has been recorded.")
            return  # No need to check responses now, as this is the first time setting the answer

            # Fetch user responses for this question
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
                # Map user selections to text answers
                user_selections = set(json.loads(user_answers_json))

                # Award points if all correct answers were selected
                if user_selections == correct_answers:
                    cursor.execute('''
                    UPDATE bonus_answers
                    SET points = ?
                    WHERE question_id = ? AND user_id = ?
                    ''', (points_value, question_id, user_id))
                    conn.commit()
                    
                    conn.commit()
                    await update_leaderboard()
                    awarded_users.append(user_id)

            if awarded_users:
                awarded_mentions = ", ".join([f"<@{user_id}>" for user_id in awarded_users])
                await message.channel.send(f"✅ Points awarded! The correct answer was: {correct_answer_text}. Users awarded: {awarded_mentions}")
            else:
                await message.channel.send(f"❌ No users selected the correct answer. The correct answer was: {correct_answer_text}.")

@bot.event
async def on_reaction_remove(reaction, user):
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
        else:
            return  # Nothing to remove

        # Map emoji to actual option
        selected_index = reactions.index(str(reaction.emoji))
        selected_option = option_split[selected_index]

        if selected_option in existing_answers:
            existing_answers.remove(selected_option)  # Remove selection

        updated_answers = json.dumps(existing_answers)

        # Update the database
        cursor.execute('''
        UPDATE bonus_answers
        SET answer = ?
        WHERE user_id = ? AND question_id = ?
        ''', (updated_answers, user.id, question_id))
        conn.commit()
    
    elif poll_type == "match_poll":
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
            DELETE FROM predictions WHERE match_id = ? AND user_id = ?
            ''', (match_id, user.id))
            conn.commit()

            await message.channel.send(f"{user.mention}, your prediction has been removed.")

        except Exception as e:
            print(f"Error removing match prediction: {e}")
    




@bot.command()
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
async def predictions(ctx):
    try:
        user_id = ctx.author.id

        # Get the next three unique match dates
        cursor.execute('''
            SELECT DISTINCT match_date
            FROM matches
            WHERE match_date >= CURRENT_DATE
            ORDER BY match_date
        ''')
        upcoming_dates = [row[0] for row in cursor.fetchall()]

        if not upcoming_dates:
            await ctx.send("There are no upcoming matches in the schedule.")
            return

        # Fetch all matches for those dates, with LEFT JOIN to include missing predictions
        cursor.execute('''
            SELECT matches.match_date, matches.team1, matches.team2, matches.match_type, predictions.pred_winner, predictions.pred_score, predictions.points
            FROM matches
            LEFT JOIN predictions 
                ON matches.id = predictions.match_id AND predictions.user_id = ?
            WHERE matches.match_date IN ({})
            ORDER BY matches.match_date, matches.id
        '''.format(','.join(['?'] * len(upcoming_dates))), (user_id, *upcoming_dates))
        matches = cursor.fetchall()

        if not matches:
            await ctx.send("There are no matches to display.")
            return

        # Prepare a confirmation message
        embed = discord.Embed(
            title="Your Predictions for Upcoming Matches",
            description="Here are the predictions you have made (or need to make) for the next scheduled days.",
            color=discord.Color.blue()
        )

        for match_date, team1, team2, match_type, pred_winner, pred_score, points in matches:
            if pred_winner:
                prediction_text = f"{pred_winner} {pred_score} (Points: {points if points else 0})"
            else:
                prediction_text = "No prediction made."

            embed.add_field(
                name=f"{team1} vs {team2} ({match_type}) - {match_date}",
                value=prediction_text,
                inline=False
            )

        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"An error occurred while fetching your predictions: {e}")


@bot.command()
async def reset_leaderboard(ctx):
    """
    Reset the leaderboard and clear all points.
    """
    # Clear leaderboard data
    cursor.execute('DELETE FROM leaderboard')
    conn.commit()

    # Reset points in predictions table
    cursor.execute('UPDATE predictions SET points = 0')
    conn.commit()

    await ctx.send("Leaderboard has been reset, and all points have been cleared!")

@bot.command()
async def voting_summary(ctx, match_date: str):
    """
    Display voting statistics for matches on a specific date.
    Args:
        match_date: Date of the matches in DD-MM format.
    """
    try:
        # Convert provided date to match format (YYYY-MM-DD)
        match_date = datetime.strptime(match_date, "%d-%m")      
        current_year = datetime.now().year
        match_date_with_year = match_date.replace(year=current_year).strftime("%Y-%m-%d")

        # Fetch matches on the specified date
        cursor.execute('''
        SELECT id, team1, team2, match_type
        FROM matches
        WHERE match_date = ?
        ''', (match_date_with_year,))  # Match the date part (DD-MM)
        matches = cursor.fetchall()

        if not matches:
            await ctx.send(f"No matches found for {match_date_with_year}!")
            return

        summary_message = f"**Voting Summary for {match_date_with_year}**\n"
        for match_id, team1, team2, match_type in matches:
            # Count votes for each option
            cursor.execute('''
            SELECT pred_winner, pred_score, COUNT(*) AS votes
            FROM predictions
            WHERE match_id = ?
            GROUP BY pred_winner, pred_score
            ORDER BY votes DESC
            ''', (match_id,))
            vote_data = cursor.fetchall()

            # Append match summary
            summary_message += f"\n**Match:** {team1} vs {team2} ({match_type.upper()}) - {match_date_with_year}\n"
            if vote_data:
                for pred_winner, pred_score, votes in vote_data:
                    summary_message += f" - {pred_winner} {pred_score}: {votes} vote(s)\n"
            else:
                summary_message += "No votes recorded for this match.\n"

        await ctx.send(summary_message)

    except ValueError:
        await ctx.send("Invalid date format! Please use DD-MM.")


@bot.command()
async def delete_match(ctx, match_id: str):
    try:
        cursor.execute('DELETE FROM matches WHERE id = ?', (match_id,))
        conn.commit()
        await ctx.send(f"Match has been deleted.")
    except ValueError:
        await ctx.send("Match not found.")

async def delete_polls(match_date: str):
    """
    Deletes all polls associated with the specified match_date.
    Args:
        match_date: The match date in YYYY-MM-DD format.
    """
    try:
        # Fetch the channel IDs where the polls are located
        poll_channel_id = 1275834751697027213  # Replace with actual channel IDs        
        channel = bot.get_channel(poll_channel_id)
        if channel is None:
            print(f"Channel with ID {poll_channel_id} not found.")
            return

        # Fetch all messages from the channel
        async for message in channel.history(limit=200):  # Adjust the limit if needed
            if (
                message.author == bot.user
                and message.embeds
            ):
                await message.delete()

        print(f"All polls for {match_date} have been successfully deleted.")

    except Exception as e:
        print(f"Error deleting polls for {match_date}: {e}")


@bot.command()
async def schedule_poll_deletion(ctx, match_date: str):
    """
    Schedules the deletion of polls for the given match_date at 4 PM UK time.
    match_date format: YYYY-MM-DD
    """
    try:
        # Convert match_date to datetime
        match_date_dt = datetime.strptime(match_date, "%Y-%m-%d")
        deletion_time_utc = uk_tz.localize(
            datetime.combine(match_date_dt, datetime.min.time()) + timedelta(hours=17)
        ).astimezone(pytz.utc)  # Convert to UTC for the scheduler

        # Schedule task
        scheduler.add_job(delete_polls, "date", run_date=deletion_time_utc, args=[match_date_dt])
        await ctx.send(f"Poll deletion for {match_date} scheduled at 5 PM UK time.")

    except Exception as e:
        await ctx.send(f"Error scheduling poll deletion: {e}")

# Run bot
bot.run(TOKEN)
