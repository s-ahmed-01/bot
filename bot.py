import asyncio
import discord
from discord.ext import commands
import sqlite3  # Replace with pymongo if you prefer MongoDB
import os
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import pytz

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

# To store active polls
active_polls = {}

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
    poll_created BOOLEAN DEFAULT FALSE, -- Track poll creation
    winner TEXT,
    score TEXT
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
    points INTEGER DEFAULT 0,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
)
''')

# Commit changes to the database
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS bonus_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    answer TEXT NOT NULL,
    UNIQUE(question_id, user_id),  -- Ensure one answer per user per question
    FOREIGN KEY (question_id) REFERENCES bonus_questions (id)
)
''')
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS bonus_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT NOT NULL,
    question_type TEXT NOT NULL CHECK (question_type IN ('MCQ', 'NUMERICAL')),
    options TEXT, -- JSON string for MCQ options, NULL for numerical questions
    correct_answer TEXT NOT NULL, -- For MCQ, store the correct option; for numerical, store the range (e.g., "14-16").
    date_posted DATE DEFAULT CURRENT_DATE,
    points INTEGER DEFAULT 1
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

# Event: Bot ready
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

# Command: Leaderboard
@bot.command()
async def leaderboard(ctx):
    cursor.execute('''
    SELECT user_id, SUM(points) as total_points
    FROM predictions
    GROUP BY user_id
    ORDER BY total_points DESC
    ''')
    leaderboard = cursor.fetchall()

    if not leaderboard:
        await ctx.send("No leaderboard data available yet!")
        return

    leaderboard_message = "🏆 **Leaderboard** 🏆\n"
    for rank, (user_id, total_points) in enumerate(leaderboard, start=1):
        user = await bot.fetch_user(user_id)
        leaderboard_message += f"{rank}. {user.name} - {total_points} points\n"

    await ctx.send(leaderboard_message)

# Command: Trivia (example)
@bot.command()
async def trivia(ctx):
    question = "What year did Discord launch?"
    options = ["2015", "2016", "2017", "2018"]
    await ctx.send(f"**Trivia Time!**\n{question}\nOptions: {', '.join(options)}")

@bot.command()
async def schedule(ctx, match_date: str, match_type: str, team1: str, team2: str):
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

        # Insert into the database with the full date
        cursor.execute('''
        INSERT INTO matches (match_date, match_type, team1, team2)
        VALUES (?, ?, ?, ?)
        ''', (match_date_with_year.strftime("%Y-%m-%d"), match_type, team1, team2))
        conn.commit()
    except ValueError:
        print("Invalid date format. Please use dd-mm.")

@bot.command()
async def create_polls(ctx):
    cursor.execute('''
    SELECT id, match_date, match_type, team1, team2, poll_created
    FROM matches
    ORDER BY match_date
    ''')
    matches = cursor.fetchall()

    if not matches:
        await ctx.send("No matches scheduled!")
        return

    current_date = None
    for match in matches:
        match_id, match_date, match_type, team1, team2, poll_created = match

        # Skip if poll already created for this match
        if poll_created:
            continue

        # Add date header if the date changes
        if match_date != current_date:
            current_date = match_date
            await ctx.send(f"**{current_date} Games**")

        # Generate score options based on match type
        if match_type == 'BO1':
            options = [f"{team1} wins", f"{team2} wins"]
        elif match_type == 'BO3':
            options = [f"{team1} 2-0", f"{team1} 2-1", f"{team2} 2-1", f"{team2} 2-0"]
        elif match_type == 'BO5':
            options = [
                f"{team1} 3-0", f"{team1} 3-1", f"{team1} 3-2",
                f"{team2} 3-2", f"{team2} 3-1", f"{team2} 3-0"
            ]

        embed = discord.Embed(title=f"Match Poll: {team1} vs {team2} ({match_type})", description="React with your prediction!", color=discord.Color.blue())
        for i, option in enumerate(options, start=1):
            embed.add_field(name=f"Option {i}", value=option, inline=False)

        # Send embed and add reactions
        poll_message = await ctx.send(embed=embed)
        numeric_emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣']  # Numeric emojis for options
        for i in range(len(options)):
            await poll_message.add_reaction(numeric_emojis[i])

        # Mark poll as created
        cursor.execute('''
        UPDATE matches
        SET poll_created = TRUE
        WHERE id = ?
        ''', (match_id,))
        conn.commit()

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return  # Ignore bot reactions

    message = reaction.message
    if message.id not in active_polls:
        return  # Ignore reactions not related to active polls

    poll_data = active_polls.pop(message.id)  # Retrieve and remove poll data
    match_id = poll_data["match_id"]
    match_type = poll_data["match_type"]
    options = poll_data["options"]
    reactions = poll_data["reactions"]

    # Determine which result was selected
    if str(reaction.emoji) not in reactions:
        await message.channel.send("Invalid reaction. Poll closed without recording a result.")
        return

    selected_index = reactions.index(str(reaction.emoji))
    result = options[selected_index]
    winner, score = result.split(" ", 1)  # Extract winner and score

    # Update the match result in the database
    cursor.execute('''
    UPDATE matches
    SET winner = ?, score = ?
    WHERE id = ?
    ''', (winner, score, match_id))
    conn.commit()

    # Calculate and update points for predictions
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
            points += 1 if match_type == "BO1" else (2 if match_type == "BO3" else 3)

            # Bonus points for correct score
            if pred_score == score:
                points += 1 if match_type == "BO3" else (2 if match_type == "BO5" else 0)

        # Update points in the predictions table
        cursor.execute('''
        UPDATE predictions
        SET points = ?
        WHERE id = ?
        ''', (points, pred_id))
    conn.commit()

    # Delete the poll message
    await message.delete()

    # Notify the result
    await message.channel.send(
        f"Result recorded for match {poll_data['team1']} vs {poll_data['team2']} ({match_type}): {winner} wins with score {score}!"
    )


@bot.command()
async def result_polls(ctx):
    # Fetch matches for the upcoming week
    today = datetime.today().date()
    one_week_later = today + timedelta(days=7)

    cursor.execute('''
    SELECT id, team1, team2, match_type
    FROM matches
    WHERE match_date BETWEEN ? AND ?
    ORDER BY match_date, id
    ''', (today, one_week_later))
    matches = cursor.fetchall()

    if not matches:
        await ctx.send("No matches found for the upcoming week.")
        return

    for match in matches:
        match_id, team1, team2, match_type = match

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

        # Create embed for the poll
        embed = discord.Embed(
            title=f"Result Poll: {team1} vs {team2} ({match_type})",
            description="React with the correct result to record it!",
            color=discord.Color.green()
        )
        for i, option in enumerate(options, start=1):
            embed.add_field(name=f"Option {i}", value=option, inline=False)

        # Send the poll message
        poll_message = await ctx.send(embed=embed)

        # Add reactions
        for reaction in reactions:
            await poll_message.add_reaction(reaction)

        # Store poll data
        active_polls[poll_message.id] = {
            "match_id": match_id,
            "team1": team1,
            "team2": team2,
            "match_type": match_type,
            "options": options,
            "reactions": reactions
        }

@bot.command()
async def show_matches(ctx):
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
async def confirm_predictions(ctx):
    try:
        user_id = ctx.author.id

        # Get the next three unique match dates
        cursor.execute('''
            SELECT DISTINCT match_date
            FROM matches
            WHERE match_date >= CURRENT_DATE
            ORDER BY match_date
            LIMIT 3
        ''')
        upcoming_dates = [row[0] for row in cursor.fetchall()]

        if not upcoming_dates:
            await ctx.send("There are no upcoming matches in the schedule.")
            return

        # Fetch all matches for those dates, with left join to include missing predictions
        cursor.execute('''
            SELECT matches.match_date, matches.team1, matches.team2, matches.match_type, predictions.pred_winner, predictions.pred_score
            FROM matches
            LEFT JOIN predictions ON matches.id = predictions.match_id AND predictions.user_id = ?
            WHERE matches.match_date IN ({})
            ORDER BY matches.match_date, matches.id
        '''.format(','.join(['?'] * len(upcoming_dates))), (user_id, *upcoming_dates))
        matches = cursor.fetchall()

        if not matches:
            await ctx.send("There are no matches to display.")
            return

        # Prepare a confirmation message
        for match_date, team1, team2, match_type, pred_winner, pred_score in matches:
            embed = discord.Embed(
                title="Your Predictions for the Upcoming Scheduled Matches",
                description="Here are the predictions you have made (or need to make) for the next scheduled days.",
                color=discord.Color.green()
            )

        embed.add_field(
            name=f"{team1} vs {team2} ({match_type}) - {match_date}",
            value=f"Your Prediction: {pred_winner} {pred_score}",
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
        # Convert provided date to match format
        match_date_formatted = datetime.strptime(match_date, "%d-%m").strftime("%d-%m-%Y")

        # Fetch matches on the specified date
        cursor.execute('''
        SELECT id, team1, team2, match_type
        FROM matches
        WHERE match_date LIKE ?
        ''', (f"%{match_date_formatted}%",))
        matches = cursor.fetchall()

        if not matches:
            await ctx.send(f"No matches found for {match_date}!")
            return

        summary_message = f"**Voting Summary for {match_date_formatted}**\n"
        for match_id, team1, team2, match_type in matches:
            # Count votes for each option
            cursor.execute('''
            SELECT pred_winner, COUNT(*) AS votes
            FROM predictions
            WHERE match_id = ?
            GROUP BY pred_winner
            ORDER BY votes DESC
            ''', (match_id,))
            vote_data = cursor.fetchall()

            summary_message += f"\n**Match:** {team1} vs {team2} ({match_type.upper()})\n"
            if vote_data:
                for pred_winner, votes in vote_data:
                    summary_message += f" - {pred_winner}: {votes} vote(s)\n"
            else:
                summary_message += "No votes recorded for this match.\n"

        await ctx.send(summary_message)

    except ValueError:
        await ctx.send("Invalid date format! Please use DD-MM.")

@bot.command()
async def delete_matches_by_date(ctx, match_date: str):
    try:
        datetime.strptime(match_date, "%d-%m-%Y")  # Validate the date
        cursor.execute('DELETE FROM matches WHERE match_date = ?', (match_date,))
        conn.commit()
        await ctx.send(f"All matches scheduled on {match_date} have been deleted.")
    except ValueError:
        await ctx.send("Invalid date format! Please use DD-MM-YYYY.")

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
            datetime.combine(match_date_dt, datetime.min.time()) + timedelta(hours=16)
        ).astimezone(pytz.utc)  # Convert to UTC for the scheduler

        # Schedule task
        scheduler.add_job(delete_polls, "date", run_date=deletion_time_utc, args=[match_date])
        await ctx.send(f"Poll deletion for {match_date} scheduled at 4 PM UK time.")

    except Exception as e:
        await ctx.send(f"Error scheduling poll deletion: {e}")

async def delete_polls(match_date: str):
    """
    Deletes all polls for the specified match_date.
    """
    try:
        # Fetch all polls for the match_date
        cursor.execute('''
            SELECT id, poll_message_id FROM matches
            WHERE match_date = ? AND poll_created = TRUE
        ''', (match_date,))
        polls = cursor.fetchall()

        if not polls:
            print(f"No polls found for {match_date}.")
            return

        # Delete polls and update the database
        for match_id, poll_message_id in polls:
            # Fetch the channel and message to delete
            channel = bot.get_channel(your_channel_id)  # Replace with your channel ID
            if channel is None:
                print("Channel not found.")
                continue

            try:
                poll_message = await channel.fetch_message(poll_message_id)
                await poll_message.delete()
                print(f"Deleted poll message {poll_message_id}.")
            except Exception as e:
                print(f"Failed to delete poll {poll_message_id}: {e}")

            # Mark poll as deleted in the database
            cursor.execute('''
                UPDATE matches
                SET poll_created = FALSE
                WHERE id = ?
            ''', (match_id,))
            conn.commit()

    except Exception as e:
        print(f"Error deleting polls for {match_date}: {e}")




# Run bot
bot.run(TOKEN)
