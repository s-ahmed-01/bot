from datetime import datetime
import discord
from discord.ext import commands
import sqlite3  # Replace with pymongo if you prefer MongoDB
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Bot setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

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

                embed = discord.Embed(title=f"Match Poll: {team1} vs {team2} ({match_type})",
                              description="React with your prediction!",
                              color=discord.Color.blue())
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
        return

    message = reaction.message
    if not message.embeds:
        return

    embed = message.embeds[0]
    if "Match Poll:" in embed.title:
        # Parse team names and match type from the title
        match_info = embed.title.replace("Match Poll: ", "").split(" ")
        match_type = match_info[-1].strip("()").upper()  # Extract match type from the last element
        team1, vs, team2 = match_info[:-1]  # Extract team1, "vs", and team2
        team1 = team1.strip()
        team2 = team2.strip()

        # Retrieve match_id for the given teams
        cursor.execute('SELECT id FROM matches WHERE team1 = ? AND team2 = ?', (team1, team2))
        match_data = cursor.fetchone()
        if not match_data:
            await message.channel.send("Error: Match data not found in the database.")
            return

        match_id = match_data[0]

        # Determine the predicted winner and score based on the reaction and match type
        option_index = ord(str(reaction.emoji)) - 127462  # Regional indicator emojis start at 127462
        pred_winner = None
        pred_score = None

        if match_type == 'BO1':
            if option_index == 0:  # Team 1 wins
                pred_winner = team1
                pred_score = "1-0"
            elif option_index == 1:  # Team 2 wins
                pred_winner = team2
                pred_score = "1-0"

        elif match_type == 'BO3':
            if option_index == 0:
                pred_winner = team1
                pred_score = "2-0"
            elif option_index == 1:
                pred_winner = team1
                pred_score = "2-1"
            elif option_index == 2:
                pred_winner = team2
                pred_score = "2-1"
            elif option_index == 3:
                pred_winner = team2
                pred_score = "2-0"

        elif match_type == 'BO5':
            if option_index == 0:
                pred_winner = team1
                pred_score = "3-0"
            elif option_index == 1:
                pred_winner = team1
                pred_score = "3-1"
            elif option_index == 2:
                pred_winner = team1
                pred_score = "3-2"
            elif option_index == 3:
                pred_winner = team2
                pred_score = "3-2"
            elif option_index == 4:
                pred_winner = team2
                pred_score = "3-1"
            elif option_index == 5:
                pred_winner = team2
                pred_score = "3-0"

        # Validate the prediction
        if not pred_winner or not pred_score:
            await message.channel.send(f"{user.name}, your prediction is invalid for this match.")
            return

        # Check if a prediction already exists for the user and match
        cursor.execute('SELECT id FROM predictions WHERE user_id = ? AND match_id = ?', (user.id, match_id))
        existing_prediction = cursor.fetchone()

        if existing_prediction:
            # Update existing prediction
            cursor.execute('''
            UPDATE predictions
            SET pred_winner = ?, pred_score = ?
            WHERE user_id = ? AND match_id = ?
            ''', (pred_winner, pred_score, user.id, match_id))
            conn.commit()
            await reaction.message.channel.send(f"{user.name}, your prediction has been updated to {pred_winner} with score {pred_score}.")
        else:
            # Insert new prediction
            cursor.execute('''
            INSERT INTO predictions (user_id, match_id, pred_winner, pred_score)
            VALUES (?, ?, ?, ?)
            ''', (user.id, match_id, pred_winner, pred_score))
            conn.commit()
            await reaction.message.channel.send(f"{user.name} voted for {pred_winner} with score {pred_score}.")


@bot.command()
async def add_result(ctx, match_id: int, winner: str, score: str):
    # Validate match ID
    cursor.execute('SELECT * FROM matches WHERE id = ?', (match_id,))
    match = cursor.fetchone()
    if not match:
        await ctx.send("Invalid match ID!")
        return

    # Update match result
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
            points += 1 if match[3] == 'BO1' else (2 if match[3] == 'BO3' else 3)

            # Bonus points for correct score
            if pred_score == score:
                points += 1 if match[3] == 'BO3' else (2 if match[3] == 'BO5' else 0)

        # Update points in the predictions table
        cursor.execute('''
        UPDATE predictions
        SET points = ?
        WHERE id = ?
        ''', (points, pred_id))
    conn.commit()

    conn.commit()

    await ctx.send(f"Result recorded for match {match_id}: {winner} wins {score}!")

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





# Run bot
bot.run(TOKEN)
