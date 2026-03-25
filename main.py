import discord
from discord.ext import commands
from discord.ui import Button, View
import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv

# ============================================================
# SETUP INICIAL
# ============================================================

load_dotenv()
TOKEN = os.getenv('ADMIN_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix='.', intents=intents)
bot.remove_command('help')

# ============================================================
# BANCO DE DADOS SQLITE
# ============================================================

DB_PATH = "admin_bot.db"

def db_connect():
    return sqlite3.connect(DB_PATH)

def db_init():
    with db_connect() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id           INTEGER PRIMARY KEY,
                guild_type         TEXT    DEFAULT 'public',
                prefix             TEXT    DEFAULT '.',
                admin_category     TEXT    DEFAULT 'ADMIN',
                log_channel        TEXT    DEFAULT 'logs-gerais',
                welcome_channel_id INTEGER DEFAULT NULL,
                welcome_message    TEXT    DEFAULT 'Bem-vindo(a) {mention} ao {server}!',
                autorole_id        INTEGER DEFAULT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS warns (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                mod_id     INTEGER NOT NULL,
                reason     TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # ── NOVO: Reaction Roles ──────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS reaction_roles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                emoji       TEXT    NOT NULL,
                role_id     INTEGER NOT NULL,
                description TEXT    DEFAULT '',
                bot_message INTEGER DEFAULT 1,
                UNIQUE(message_id, emoji)
            )
        """)
        # Migrações seguras
        for col in ["autorole_id INTEGER DEFAULT NULL"]:
            try:
                c.execute(f"ALTER TABLE guild_config ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        conn.commit()

def guild_config(guild_id: int) -> dict:
    with db_connect() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,))
        row = c.fetchone()
        if not row:
            c.execute("INSERT INTO guild_config (guild_id) VALUES (?)", (guild_id,))
            conn.commit()
            c.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,))
            row = c.fetchone()
        cols = [d[0] for d in c.description]
        return dict(zip(cols, row))

def update_config(guild_id: int, key: str, value):
    with db_connect() as conn:
        conn.execute(f"UPDATE guild_config SET {key} = ? WHERE guild_id = ?", (value, guild_id))
        conn.commit()

# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

async def get_admin_category(guild: discord.Guild) -> discord.CategoryChannel:
    cfg = guild_config(guild.id)
    cat = discord.utils.get(guild.categories, name=cfg['admin_category'])
    if not cat:
        cat = await guild.create_category(cfg['admin_category'])
    return cat

async def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    cfg = guild_config(guild.id)
    category = await get_admin_category(guild)
    channel = discord.utils.get(category.text_channels, name=cfg['log_channel'])
    if not channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        channel = await guild.create_text_channel(cfg['log_channel'], category=category, overwrites=overwrites)
    return channel

def log_embed(title: str, color: int, **fields) -> discord.Embed:
    embed = discord.Embed(title=title, color=color, timestamp=datetime.utcnow())
    for name, value in fields.items():
        embed.add_field(name=name, value=str(value) if value else "—", inline=True)
    embed.set_footer(text=f"🕐 {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC")
    return embed

# ============================================================
# ON READY
# ============================================================

@bot.event
async def on_ready():
    db_init()
    bot.add_view(TicketView())
    bot.add_view(CloseTicketView())
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="os dois servidores 👁️"
    ))
    guilds_list = ', '.join([f"{g.name} ({g.id})" for g in bot.guilds])
    print(f"🛡️  Admin Bot Online: {bot.user}")
    print(f"📡 Servidores: {guilds_list}")

# ============================================================
# TRATAMENTO GLOBAL DE ERROS
# ============================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Você não tem permissão para isso.", delete_after=5)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Membro não encontrado.", delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Argumento faltando: `{error.param.name}`", delete_after=5)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Argumento inválido.", delete_after=5)
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Aguarde {error.retry_after:.1f}s.", delete_after=5)
    else:
        print(f"[ERRO] Comando '{ctx.command}' em '{ctx.guild}': {error}")

# ============================================================
# CONFIGURAÇÃO DO SERVIDOR
# ============================================================

@bot.command(name="config")
@commands.has_permissions(administrator=True)
async def config_cmd(ctx, chave: str = None, *, valor: str = None):
    cfg = guild_config(ctx.guild.id)

    if not chave:
        embed = discord.Embed(title=f"⚙️ Config — {ctx.guild.name}", color=0x2f3136)
        embed.add_field(name="Tipo",            value=cfg['guild_type'],     inline=True)
        embed.add_field(name="Prefixo",         value=cfg['prefix'],         inline=True)
        embed.add_field(name="Categoria Admin", value=cfg['admin_category'], inline=True)
        embed.add_field(name="Canal de Logs",   value=cfg['log_channel'],    inline=True)
        wc = ctx.guild.get_channel(cfg['welcome_channel_id']) if cfg['welcome_channel_id'] else "Não configurado"
        embed.add_field(name="Boas-vindas",     value=wc,                    inline=True)
        ar = ctx.guild.get_role(cfg['autorole_id']) if cfg['autorole_id'] else "Não configurado"
        embed.add_field(name="Autorole",        value=ar,                    inline=True)
        embed.add_field(name="Msg Boas-vindas", value=cfg['welcome_message'], inline=False)
        return await ctx.send(embed=embed)

    key_map = {
        "tipo":            "guild_type",
        "categoria":       "admin_category",
        "log_canal":       "log_channel",
        "boas_vindas_msg": "welcome_message",
    }

    if chave == "boas_vindas_canal":
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else None
        if not ch:
            return await ctx.send("❌ Mencione o canal: `.config boas_vindas_canal #canal`")
        update_config(ctx.guild.id, "welcome_channel_id", ch.id)
        return await ctx.send(f"✅ Canal de boas-vindas: {ch.mention}")

    elif chave == "autorole":
        role = ctx.message.role_mentions[0] if ctx.message.role_mentions else \
               discord.utils.get(ctx.guild.roles, name=valor)
        if not role:
            return await ctx.send("❌ Cargo não encontrado. Use `.config autorole @Cargo` ou o nome exato.")
        update_config(ctx.guild.id, "autorole_id", role.id)
        return await ctx.send(f"✅ Autorole configurado: {role.mention}")

    elif chave == "autorole_remover":
        update_config(ctx.guild.id, "autorole_id", None)
        return await ctx.send("✅ Autorole desativado.")

    if chave not in key_map:
        opcoes = ', '.join(list(key_map.keys()) + ['boas_vindas_canal', 'autorole', 'autorole_remover'])
        return await ctx.send(f"❌ Chave inválida. Opções: {opcoes}")

    if chave == "tipo" and valor not in ("public", "private"):
        return await ctx.send("❌ Tipo deve ser `public` ou `private`.")

    update_config(ctx.guild.id, key_map[chave], valor)
    await ctx.send(f"✅ `{chave}` → `{valor}`")

# ============================================================
# MODERAÇÃO
# ============================================================

@bot.command(name="kick", aliases=["expulsar"])
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    if member == ctx.author:
        return await ctx.send("❌ Você não pode se expulsar.")
    if member.top_role >= ctx.author.top_role:
        return await ctx.send("❌ Hierarquia de cargos não permite.")
    await member.kick(reason=reason)
    await ctx.send(f"👢 **{member.name}** foi expulso. Motivo: {reason or 'Não informado'}")

@bot.command(name="ban", aliases=["banir"])
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    if member == ctx.author:
        return await ctx.send("❌ Você não pode se banir.")
    if member.top_role >= ctx.author.top_role:
        return await ctx.send("❌ Hierarquia de cargos não permite.")
    await member.ban(reason=reason)
    await ctx.send(f"🚫 **{member.name}** levou BAN! Motivo: {reason or 'Não informado'}")

@bot.command(name="unban", aliases=["desbanir"])
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, user_name):
    banned_users = [entry async for entry in ctx.guild.bans()]
    for ban_entry in banned_users:
        user = ban_entry.user
        if user.name == user_name or str(user.id) == user_name:
            await ctx.guild.unban(user)
            return await ctx.send(f"✅ **{user.name}** foi perdoado.")
    await ctx.send(f"❌ Não achei `{user_name}` na lista de banidos.")

@bot.command(name="timeout", aliases=["silenciar"])
@commands.has_permissions(moderate_members=True)
async def timeout_cmd(ctx, member: discord.Member, minutos: int, *, reason=None):
    if member.top_role >= ctx.author.top_role:
        return await ctx.send("❌ Hierarquia de cargos não permite.")
    until = discord.utils.utcnow() + timedelta(minutes=minutos)
    await member.timeout(until, reason=reason)
    await ctx.send(f"🔇 **{member.name}** silenciado por {minutos} min. Motivo: {reason or 'Não informado'}")

@bot.command(name="lock", aliases=["trancar"])
@commands.has_permissions(manage_channels=True)
async def lock(ctx):
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 **Canal TRANCADO.**")

@bot.command(name="unlock", aliases=["destrancar"])
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send("🔓 **Canal DESTRANCADO.**")

@bot.command(name="limpar", aliases=["clear"])
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    if not 1 <= amount <= 200:
        return await ctx.send("❌ Use um valor entre 1 e 200.")
    await ctx.channel.purge(limit=amount + 1)

# ============================================================
# SISTEMA DE WARNS
# ============================================================

@bot.command(name="warn", aliases=["avisar"])
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason="Não informado"):
    if member.bot:
        return await ctx.send("❌ Não dá pra advertir um bot.")
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO warns (guild_id, user_id, mod_id, reason) VALUES (?, ?, ?, ?)",
            (ctx.guild.id, member.id, ctx.author.id, reason)
        )
        conn.commit()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
        total = c.fetchone()[0]

    embed = discord.Embed(title="⚠️ Aviso Aplicado", color=0xffcc00)
    embed.add_field(name="Membro",    value=member.mention)
    embed.add_field(name="Moderador", value=ctx.author.mention)
    embed.add_field(name="Motivo",    value=reason, inline=False)
    embed.set_footer(text=f"Total de warns: {total}")
    await ctx.send(embed=embed)

    try:
        await member.send(
            f"⚠️ Você recebeu um aviso em **{ctx.guild.name}**.\n"
            f"Motivo: {reason}\nTotal de warns: {total}"
        )
    except discord.Forbidden:
        pass

@bot.command(name="warns", aliases=["avisos"])
@commands.has_permissions(manage_messages=True)
async def warns_cmd(ctx, member: discord.Member):
    with db_connect() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, mod_id, reason, created_at FROM warns "
            "WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC",
            (ctx.guild.id, member.id)
        )
        rows = c.fetchall()

    if not rows:
        return await ctx.send(f"✅ **{member.name}** não tem warns.")

    embed = discord.Embed(title=f"⚠️ Warns de {member.name}", color=0xffcc00)
    for row in rows[:10]:
        wid, mod_id, reason, created_at = row
        mod = ctx.guild.get_member(mod_id)
        embed.add_field(
            name=f"#{wid} — {created_at[:10]}",
            value=f"Mod: {mod.mention if mod else mod_id}\nMotivo: {reason}",
            inline=False
        )
    embed.set_footer(text=f"Total: {len(rows)} warn(s)")
    await ctx.send(embed=embed)

@bot.command(name="clearwarns", aliases=["limparwarns"])
@commands.has_permissions(administrator=True)
async def clearwarns(ctx, member: discord.Member):
    with db_connect() as conn:
        conn.execute("DELETE FROM warns WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
        conn.commit()
    await ctx.send(f"✅ Warns de **{member.name}** removidos.")

# ============================================================
# INFORMAÇÕES
# ============================================================

@bot.command(name="userinfo")
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    embed = discord.Embed(title=f"👤 {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",              value=member.id,                             inline=True)
    embed.add_field(name="Apelido",         value=member.nick or "—",                    inline=True)
    embed.add_field(name="Bot",             value="Sim" if member.bot else "Não",         inline=True)
    embed.add_field(name="Conta criada",    value=member.created_at.strftime("%d/%m/%Y"), inline=True)
    embed.add_field(name="Entrou",          value=member.joined_at.strftime("%d/%m/%Y"),  inline=True)
    embed.add_field(name=f"Cargos ({len(roles)})", value=" ".join(roles[:15]) or "—",    inline=False)
    await ctx.send(embed=embed)

@bot.command(name="serverinfo")
async def serverinfo(ctx):
    g = ctx.guild
    cfg = guild_config(g.id)
    embed = discord.Embed(title=f"🏠 {g.name}", color=0x2f3136)
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="ID",        value=g.id,                               inline=True)
    embed.add_field(name="Tipo",      value=cfg['guild_type'],                   inline=True)
    embed.add_field(name="Dono",      value=g.owner.mention if g.owner else "—", inline=True)
    embed.add_field(name="Membros",   value=g.member_count,                      inline=True)
    embed.add_field(name="Canais",    value=len(g.text_channels),                inline=True)
    embed.add_field(name="Cargos",    value=len(g.roles),                        inline=True)
    embed.add_field(name="Criado em", value=g.created_at.strftime("%d/%m/%Y"),   inline=True)
    await ctx.send(embed=embed)

# ============================================================
# BOAS-VINDAS, AUTOROLE E LOG DE ENTRADA/SAÍDA
# ============================================================

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    cfg = guild_config(guild.id)

    if cfg['welcome_channel_id']:
        channel = guild.get_channel(cfg['welcome_channel_id'])
        if channel:
            msg = (cfg['welcome_message']
                   .replace("{mention}", member.mention)
                   .replace("{server}", guild.name)
                   .replace("{name}", member.name))
            embed = discord.Embed(description=msg, color=0x00ff88)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"Membro #{guild.member_count}")
            await channel.send(embed=embed)

    if cfg['autorole_id']:
        role = guild.get_role(cfg['autorole_id'])
        if role:
            try:
                await member.add_roles(role, reason="Autorole automático")
            except discord.Forbidden:
                pass

    log_ch = await get_log_channel(guild)
    if log_ch:
        age = (datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
        embed = log_embed("📥 Membro Entrou", 0x00ff88,
                          Membro=f"{member} ({member.id})",
                          Conta=f"{age} dias de idade",
                          Total=f"{guild.member_count} membros")
        embed.set_thumbnail(url=member.display_avatar.url)
        await log_ch.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    log_ch = await get_log_channel(guild)
    if log_ch:
        roles = ", ".join([r.name for r in member.roles if r.name != "@everyone"]) or "—"
        embed = log_embed("📤 Membro Saiu", 0xff4444,
                          Membro=f"{member} ({member.id})",
                          Cargos=roles)
        embed.set_thumbnail(url=member.display_avatar.url)
        await log_ch.send(embed=embed)

# ============================================================
# LOGS DE MEMBROS (CARGO, APELIDO, TIMEOUT)
# ============================================================

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    log_ch = await get_log_channel(before.guild)
    if not log_ch:
        return

    if before.nick != after.nick:
        embed = log_embed("✏️ Apelido Alterado", 0xffa500,
                          Membro=after.mention,
                          Antes=before.nick or "—",
                          Depois=after.nick or "—")
        await log_ch.send(embed=embed)

    roles_antes  = set(before.roles)
    roles_depois = set(after.roles)
    adicionados  = roles_depois - roles_antes
    removidos    = roles_antes  - roles_depois

    if adicionados:
        embed = log_embed("🟢 Cargo Adicionado", 0x00cc66,
                          Membro=after.mention,
                          Cargo=", ".join([r.mention for r in adicionados]))
        await log_ch.send(embed=embed)

    if removidos:
        embed = log_embed("🔴 Cargo Removido", 0xff4444,
                          Membro=after.mention,
                          Cargo=", ".join([r.name for r in removidos]))
        await log_ch.send(embed=embed)

    if before.timed_out_until != after.timed_out_until:
        if after.timed_out_until:
            embed = log_embed("🔇 Timeout Aplicado", 0xff6600,
                              Membro=after.mention,
                              Até=after.timed_out_until.strftime("%d/%m/%Y %H:%M UTC"))
        else:
            embed = log_embed("🔊 Timeout Removido", 0x00ff88, Membro=after.mention)
        await log_ch.send(embed=embed)

# ============================================================
# LOGS DE MENSAGENS
# ============================================================

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    log_ch = await get_log_channel(message.guild)
    if log_ch:
        content = message.content[:1020] if message.content else "📎 Arquivo/Embed"
        embed = log_embed("🗑️ Mensagem Deletada", 0xff0000,
                          Autor=message.author.mention,
                          Canal=message.channel.mention,
                          Conteúdo=content)
        await log_ch.send(embed=embed)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or before.content == after.content:
        return
    log_ch = await get_log_channel(before.guild)
    if log_ch:
        embed = log_embed("✏️ Mensagem Editada", 0xffa500,
                          Autor=before.author.mention,
                          Canal=before.channel.mention,
                          Antes=before.content[:500] or "—",
                          Depois=after.content[:500] or "—")
        embed.add_field(name="Link", value=f"[Ver mensagem]({after.jump_url})", inline=False)
        await log_ch.send(embed=embed)

# ============================================================
# LOGS DE VOZ
# ============================================================

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    log_ch = await get_log_channel(member.guild)
    if not log_ch:
        return

    if before.channel is None and after.channel is not None:
        embed = log_embed("🔊 Entrou em Voz", 0x00ff88,
                          Membro=member.mention, Canal=after.channel.name)
    elif before.channel is not None and after.channel is None:
        embed = log_embed("🔇 Saiu de Voz", 0xff4444,
                          Membro=member.mention, Canal=before.channel.name)
    elif before.channel != after.channel:
        embed = log_embed("🔀 Trocou Canal de Voz", 0xffa500,
                          Membro=member.mention,
                          De=before.channel.name,
                          Para=after.channel.name)
    else:
        return
    await log_ch.send(embed=embed)

# ============================================================
# LOGS DE CANAIS
# ============================================================

@bot.event
async def on_guild_channel_create(channel):
    log_ch = await get_log_channel(channel.guild)
    if log_ch:
        tipo = "texto" if isinstance(channel, discord.TextChannel) else \
               "voz"   if isinstance(channel, discord.VoiceChannel) else "categoria"
        embed = log_embed("📁 Canal Criado", 0x00cc66,
                          Nome=channel.name, Tipo=tipo,
                          Categoria=channel.category.name if channel.category else "—")
        await log_ch.send(embed=embed)

@bot.event
async def on_guild_channel_delete(channel):
    log_ch = await get_log_channel(channel.guild)
    if log_ch:
        embed = log_embed("🗑️ Canal Deletado", 0xff0000,
                          Nome=channel.name,
                          Categoria=channel.category.name if channel.category else "—")
        await log_ch.send(embed=embed)

# ============================================================
# LOGS DE BAN / UNBAN
# ============================================================

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    log_ch = await get_log_channel(guild)
    if log_ch:
        embed = log_embed("🚫 Membro Banido", 0xff0000,
                          Usuário=f"{user} ({user.id})")
        embed.set_thumbnail(url=user.display_avatar.url)
        await log_ch.send(embed=embed)

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    log_ch = await get_log_channel(guild)
    if log_ch:
        embed = log_embed("✅ Membro Desbanido", 0x00ff88,
                          Usuário=f"{user} ({user.id})")
        await log_ch.send(embed=embed)

# ============================================================
# SISTEMA DE TICKETS
# ============================================================

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Abrir Ticket", style=discord.ButtonStyle.green, custom_id="criar_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        category = await get_admin_category(guild)
        channel_name = f"ticket-{interaction.user.name.lower().replace(' ', '-')}"
        existing = discord.utils.get(guild.text_channels, name=channel_name)

        if existing:
            return await interaction.response.send_message(
                f"❌ Você já tem um ticket aberto: {existing.mention}", ephemeral=True
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user:   discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
        await interaction.response.send_message(f"✅ Ticket criado: {channel.mention}", ephemeral=True)

        embed = discord.Embed(
            title="🎫 Suporte Aberto",
            description=f"Olá {interaction.user.mention}! Descreva seu caso.\nA staff atenderá em breve.",
            color=0x00ff88
        )
        await channel.send(f"{interaction.user.mention}", embed=embed, view=CloseTicketView())


class CloseTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Fechar Ticket", style=discord.ButtonStyle.red, custom_id="fechar_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚠️ Fechando em 5 segundos...")
        await asyncio.sleep(5)
        await interaction.channel.delete()


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    await ctx.message.delete()
    embed = discord.Embed(
        title="🎫 Central de Suporte",
        description="Clique abaixo para abrir um ticket privado com a Staff.",
        color=0x2f3136
    )
    embed.set_footer(text=ctx.guild.name)
    await ctx.send(embed=embed, view=TicketView())

# ============================================================
# REACTION ROLES
# ============================================================
#
#  FLUXO DE USO:
#  1) .rr criar #canal Título da mensagem | Descrição opcional
#       → Bot envia o embed no canal e retorna o ID da mensagem
#
#  2) .rr add <ID_msg> <emoji> @Cargo Descrição opcional
#       → Adiciona a reação na mensagem e salva no banco
#       → Pode repetir quantas vezes quiser para mais cargos
#
#  3) .rr lista         → Mostra todos os painéis configurados
#     .rr info <ID_msg> → Detalha as reações de uma mensagem
#     .rr remover <ID_msg> <emoji>   → Remove um par emoji↔cargo
#     .rr deletar <ID_msg>           → Remove TODOS os pares da msg
#
# ============================================================

def _rr_get(message_id: int, emoji: str):
    """Retorna a linha do banco para (message_id, emoji), ou None."""
    with db_connect() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?",
            (message_id, emoji)
        )
        return c.fetchone()

def _rr_list_by_message(message_id: int):
    with db_connect() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT emoji, role_id, description FROM reaction_roles WHERE message_id = ?",
            (message_id,)
        )
        return c.fetchall()

def _rr_list_by_guild(guild_id: int):
    with db_connect() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT DISTINCT message_id, channel_id FROM reaction_roles WHERE guild_id = ?",
            (guild_id,)
        )
        return c.fetchall()


async def _rr_rebuild_embed(guild: discord.Guild, message: discord.Message):
    """Recria o embed da mensagem de reaction roles com os pares atuais.
    Só edita se a mensagem foi criada pelo bot (bot_message = 1)."""
    rows = _rr_list_by_message(message.id)
    if not rows:
        return

    # Verifica se é uma mensagem do bot (pode editar o embed)
    with db_connect() as conn:
        c = conn.cursor()
        c.execute("SELECT bot_message FROM reaction_roles WHERE message_id = ? LIMIT 1", (message.id,))
        row = c.fetchone()
        if not row or not row[0]:
            return  # Mensagem de outro usuário — não edita

    if message.author.id != guild.me.id:
        return  # Proteção extra

    original = message.embeds[0] if message.embeds else None

    # Separa título base e corpo de opções (ignora a lista antiga se já existia)
    if original and original.title:
        titulo = original.title
        # Pega só a parte antes das opções (antes do primeiro \n\n━)
        desc_original = original.description or ""
        if "\n\n━" in desc_original:
            descricao = desc_original.split("\n\n━")[0]
        else:
            descricao = desc_original
    else:
        titulo    = "🎭 Escolha seu Cargo"
        descricao = "Reaja com um emoji abaixo para receber o cargo correspondente."

    # Monta as linhas de opções com separador visual
    linhas = ["━━━━━━━━━━━━━━━━━━━━━━━━"]
    for emoji, role_id, desc in rows:
        role = guild.get_role(role_id)
        nome_cargo = role.mention if role else f"`ID {role_id}`"
        linha = f"{emoji}  •  {nome_cargo}"
        if desc:
            linha += f"\n┗ *{desc}*"
        linhas.append(linha)
    linhas.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    embed = discord.Embed(
        title=titulo,
        description=descricao + "\n\n" + "\n".join(linhas),
        color=0x5865F2
    )
    embed.set_footer(text=f"✅ Reaja para ganhar o cargo  •  ❌ Remova para perder")
    await message.edit(embed=embed)


@bot.group(name="rr", invoke_without_command=True)
@commands.has_permissions(manage_roles=True)
async def rr(ctx):
    embed = discord.Embed(title="🎭 Reaction Roles — Comandos", color=0x5865F2)
    embed.add_field(name="📋 Criar painel novo", inline=False,
        value="`.rr criar #canal Título | Descrição`\n→ Bot cria o embed e você vincula os cargos")
    embed.add_field(name="🔗 Vincular em msg existente", inline=False,
        value="`.rr vincular <ID_msg> <emoji> @Cargo [desc]`\n→ Use numa mensagem **sua** já enviada")
    embed.add_field(name="➕ Adicionar cargo (painel do bot)", inline=False,
        value="`.rr add <ID_msg> <emoji> @Cargo [desc]`\n→ Adiciona cargo e atualiza o embed automaticamente")
    embed.add_field(name="🗑️ Remover / Limpar", inline=False,
        value="`.rr remover <ID> <emoji>` — Remove um par\n"
              "`.rr deletar <ID>` — Remove todos os pares")
    embed.add_field(name="📊 Consultar", inline=False,
        value="`.rr lista` — Todos os painéis\n`.rr info <ID>` — Detalhes de um painel")
    await ctx.send(embed=embed)


@rr.command(name="criar")
@commands.has_permissions(manage_roles=True)
async def rr_criar(ctx, canal: discord.TextChannel, *, conteudo: str):
    """
    Cria o embed de reaction roles num canal.
    Use | para separar título e descrição:
      .rr criar #cargos 🎭 Escolha seu Cargo | Selecione abaixo!
    """
    if "|" in conteudo:
        titulo, descricao = [p.strip() for p in conteudo.split("|", 1)]
    else:
        titulo    = conteudo.strip()
        descricao = "Selecione um emoji abaixo para receber o cargo correspondente."

    embed = discord.Embed(title=titulo, description=descricao, color=0x5865F2)
    embed.set_footer(text="✅ Reaja para ganhar o cargo  •  ❌ Remova para perder")
    msg = await canal.send(embed=embed)

    confirm = discord.Embed(
        title="✅ Painel criado!",
        description=(
            f"Canal: {canal.mention}\n"
            f"🆔 **ID:** `{msg.id}`\n\n"
            f"**Próximo passo:** adicione os cargos com\n"
            f"`.rr add {msg.id} 🎮 @Cargo Descrição`\n\n"
            f"*Repita para cada cargo que quiser adicionar.*"
        ),
        color=0x00ff88
    )
    await ctx.send(embed=confirm)


@rr.command(name="add")
@commands.has_permissions(manage_roles=True)
async def rr_add(ctx, message_id: int, emoji: str, role: discord.Role, *, descricao: str = ""):
    """
    Adiciona um par emoji↔cargo a uma mensagem existente.
    Exemplo: .rr add 123456789 🎮 @Jogadores Amantes de jogos
    """
    # Localiza a mensagem em qualquer canal do servidor
    target_msg = None
    for ch in ctx.guild.text_channels:
        try:
            target_msg = await ch.fetch_message(message_id)
            target_channel = ch
            break
        except (discord.NotFound, discord.Forbidden):
            continue

    if not target_msg:
        return await ctx.send("❌ Mensagem não encontrada. Verifique o ID.")

    # Verifica se o bot consegue gerenciar esse cargo
    if role >= ctx.guild.me.top_role:
        return await ctx.send("❌ Esse cargo está acima do meu cargo na hierarquia.")

    # Salva no banco
    with db_connect() as conn:
        try:
            conn.execute(
                "INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id, description) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ctx.guild.id, target_channel.id, message_id, emoji, role.id, descricao)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return await ctx.send(f"❌ O emoji {emoji} já está configurado nessa mensagem. Use `.rr remover` primeiro.")

    # Adiciona a reação na mensagem
    try:
        await target_msg.add_reaction(emoji)
    except discord.HTTPException:
        with db_connect() as conn:
            conn.execute("DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji))
            conn.commit()
        return await ctx.send("❌ Emoji inválido ou não encontrado. Use emojis padrão ou do servidor.")

    # Atualiza o embed com o novo cargo
    await _rr_rebuild_embed(ctx.guild, target_msg)

    await ctx.send(f"✅ {emoji} → {role.mention} adicionado com sucesso!", delete_after=8)
    await ctx.message.delete(delay=8)


@rr.command(name="vincular")
@commands.has_permissions(manage_roles=True)
async def rr_vincular(ctx, message_id: int, emoji: str, role: discord.Role, *, descricao: str = ""):
    """
    Vincula um emoji↔cargo a uma mensagem QUALQUER (inclusive mensagens suas).
    O bot só adiciona a reação — NÃO edita o texto da mensagem.
    Exemplo: .rr vincular 123456789 🎮 @Jogadores Amantes de jogos
    """
    target_msg = None
    target_channel = None
    for ch in ctx.guild.text_channels:
        try:
            target_msg = await ch.fetch_message(message_id)
            target_channel = ch
            break
        except (discord.NotFound, discord.Forbidden):
            continue

    if not target_msg:
        return await ctx.send("❌ Mensagem não encontrada. Verifique o ID e se estou no canal.")

    if role >= ctx.guild.me.top_role:
        return await ctx.send("❌ Esse cargo está acima do meu cargo na hierarquia.")

    with db_connect() as conn:
        try:
            conn.execute(
                "INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id, description, bot_message) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",  # bot_message=0 → não edita o embed
                (ctx.guild.id, target_channel.id, message_id, emoji, role.id, descricao)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return await ctx.send(f"❌ O emoji {emoji} já está vinculado nessa mensagem.")

    try:
        await target_msg.add_reaction(emoji)
    except discord.HTTPException:
        with db_connect() as conn:
            conn.execute("DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji))
            conn.commit()
        return await ctx.send("❌ Emoji inválido. Use emojis padrão ou do servidor.")

    embed = discord.Embed(
        title="🔗 Cargo vinculado!",
        description=(
            f"{emoji} → {role.mention}\n"
            f"Mensagem: [clique aqui](https://discord.com/channels/{ctx.guild.id}/{target_channel.id}/{message_id})\n"
            + (f"Descrição: *{descricao}*" if descricao else "")
        ),
        color=0x00ff88
    )
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete(delay=10)


@commands.has_permissions(manage_roles=True)
async def rr_remover(ctx, message_id: int, emoji: str):
    """Remove um par emoji↔cargo de uma mensagem."""
    row = _rr_get(message_id, emoji)
    if not row:
        return await ctx.send("❌ Par não encontrado para esse emoji/mensagem.")

    with db_connect() as conn:
        conn.execute("DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji))
        conn.commit()

    # Remove a reação da mensagem
    for ch in ctx.guild.text_channels:
        try:
            msg = await ch.fetch_message(message_id)
            await msg.clear_reaction(emoji)
            await _rr_rebuild_embed(ctx.guild, msg)
            break
        except (discord.NotFound, discord.Forbidden):
            continue

    await ctx.send(f"✅ Emoji {emoji} removido do painel `{message_id}`.")


@rr.command(name="deletar")
@commands.has_permissions(manage_roles=True)
async def rr_deletar(ctx, message_id: int):
    """Remove TODOS os pares de uma mensagem e limpa as reações."""
    rows = _rr_list_by_message(message_id)
    if not rows:
        return await ctx.send("❌ Nenhum cargo configurado nessa mensagem.")

    with db_connect() as conn:
        conn.execute("DELETE FROM reaction_roles WHERE message_id = ?", (message_id,))
        conn.commit()

    for ch in ctx.guild.text_channels:
        try:
            msg = await ch.fetch_message(message_id)
            await msg.clear_reactions()
            break
        except (discord.NotFound, discord.Forbidden):
            continue

    await ctx.send(f"✅ Todos os cargos do painel `{message_id}` foram removidos.")


@rr.command(name="lista")
@commands.has_permissions(manage_roles=True)
async def rr_lista(ctx):
    """Lista todos os painéis de reaction roles do servidor."""
    mensagens = _rr_list_by_guild(ctx.guild.id)
    if not mensagens:
        return await ctx.send("📭 Nenhum painel de reaction roles configurado.")

    embed = discord.Embed(title="🎭 Painéis de Reaction Roles", color=0x5865F2)
    for msg_id, ch_id in mensagens:
        ch = ctx.guild.get_channel(ch_id)
        rows = _rr_list_by_message(msg_id)
        pares = ", ".join([r[0] for r in rows]) or "—"
        embed.add_field(
            name=f"ID: {msg_id}",
            value=f"Canal: {ch.mention if ch else ch_id}\n"
                  f"Emojis: {pares} ({len(rows)} cargo(s))\n"
                  f"[Ver mensagem](https://discord.com/channels/{ctx.guild.id}/{ch_id}/{msg_id})",
            inline=False
        )
    await ctx.send(embed=embed)


@rr.command(name="info")
@commands.has_permissions(manage_roles=True)
async def rr_info(ctx, message_id: int):
    """Mostra todos os pares emoji↔cargo de uma mensagem."""
    rows = _rr_list_by_message(message_id)
    if not rows:
        return await ctx.send("❌ Nenhum cargo configurado nessa mensagem.")

    embed = discord.Embed(title=f"🎭 Pares — Painel {message_id}", color=0x5865F2)
    for emoji, role_id, desc in rows:
        role = ctx.guild.get_role(role_id)
        embed.add_field(
            name=f"{emoji} → {role.name if role else f'ID {role_id}'}",
            value=desc or "Sem descrição",
            inline=False
        )
    await ctx.send(embed=embed)


# ── Eventos de reação (coração do sistema) ───────────────────

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Atribui o cargo quando o usuário reage."""
    if payload.user_id == bot.user.id:
        return  # Ignora reações do próprio bot

    emoji = str(payload.emoji)
    row = _rr_get(payload.message_id, emoji)
    if not row:
        return  # Não é uma reaction role

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return

    role = guild.get_role(row[0])
    if role:
        try:
            await member.add_roles(role, reason="Reaction Role")
        except discord.Forbidden:
            pass  # Bot sem permissão


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """Remove o cargo quando o usuário tira a reação."""
    if payload.user_id == bot.user.id:
        return

    emoji = str(payload.emoji)
    row = _rr_get(payload.message_id, emoji)
    if not row:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return

    role = guild.get_role(row[0])
    if role:
        try:
            await member.remove_roles(role, reason="Reaction Role removido")
        except discord.Forbidden:
            pass

# ============================================================
# EXTRAS
# ============================================================

@bot.command()
@commands.has_permissions(manage_events=True)
async def evento(ctx):
    def check(m): return m.author == ctx.author and m.channel == ctx.channel
    try:
        await ctx.send("📅 **1.** Nome do evento?")
        nome = (await bot.wait_for('message', check=check, timeout=60)).content
        await ctx.send("**2.** Descrição?")
        desc = (await bot.wait_for('message', check=check, timeout=60)).content
        await ctx.send("**3.** Daqui a quantas horas? (Número)")
        horas = int((await bot.wait_for('message', check=check, timeout=60)).content)
        start = datetime.now().astimezone() + timedelta(hours=horas)
        event = await ctx.guild.create_scheduled_event(
            name=nome, description=desc,
            start_time=start, end_time=start + timedelta(hours=2),
            channel=ctx.guild.voice_channels[0] if ctx.guild.voice_channels else None,
            entity_type=discord.EntityType.voice,
            privacy_level=discord.PrivacyLevel.guild_only
        )
        await ctx.send(f"✅ Evento criado: {event.url}")
    except asyncio.TimeoutError:
        await ctx.send("❌ Tempo esgotado.")
    except Exception as e:
        await ctx.send(f"❌ Erro: {e}")


@bot.command()
@commands.has_permissions(administrator=True)
async def say(ctx, *, mensagem):
    await ctx.message.delete()
    if "|" in mensagem:
        titulo, conteudo = mensagem.split("|", 1)
        embed = discord.Embed(title=titulo.strip(), description=conteudo.strip(), color=0x2f3136)
        embed.set_footer(text=ctx.guild.name)
        await ctx.send(embed=embed)
    else:
        await ctx.send(mensagem)

# ============================================================
# HELP
# ============================================================

@bot.command(name="help", aliases=["ajuda"])
async def help_command(ctx):
    embed = discord.Embed(title="🛡️ Central de Comando", color=0x000000)
    embed.add_field(name="🔨 Moderação", inline=False, value=(
        "`.ban @user [motivo]` — Banir\n"
        "`.kick @user [motivo]` — Expulsar\n"
        "`.unban nome/ID` — Desbanir\n"
        "`.timeout @user mins [motivo]` — Silenciar temporariamente\n"
        "`.lock` / `.unlock` — Trancar/Destrancar canal\n"
        "`.limpar N` — Apagar N mensagens (máx 200)"
    ))
    embed.add_field(name="⚠️ Warns", inline=False, value=(
        "`.warn @user [motivo]` — Advertir membro\n"
        "`.warns @user` — Ver histórico de warns\n"
        "`.clearwarns @user` — Apagar todos os warns"
    ))
    embed.add_field(name="ℹ️ Informações", inline=False, value=(
        "`.userinfo [@user]` — Perfil completo do membro\n"
        "`.serverinfo` — Informações do servidor"
    ))
    embed.add_field(name="🎭 Reaction Roles", inline=False, value=(
        "`.rr criar #canal Título | Desc` — Criar painel (embed bonito)\n"
        "`.rr vincular <ID> <emoji> @Cargo [desc]` — Vincular em msg sua\n"
        "`.rr add <ID> <emoji> @Cargo [desc]` — Adicionar cargo ao painel\n"
        "`.rr remover <ID> <emoji>` — Remover par\n"
        "`.rr deletar <ID>` — Remover todos os pares\n"
        "`.rr lista` — Ver todos os painéis\n"
        "`.rr info <ID>` — Detalhar um painel"
    ))
    embed.add_field(name="⚙️ Sistemas", inline=False, value=(
        "`.setup_ticket` — Criar painel de suporte\n"
        "`.evento` — Criar evento agendado interativo\n"
        "`.say texto` — Bot fala (use | para embed)\n"
        "`.config` — Ver/editar configurações do servidor"
    ))
    embed.set_footer(text="Logs automáticos na categoria ADMIN • Autorole e boas-vindas via .config")
    await ctx.send(embed=embed)

# ============================================================
# RUN
# ============================================================

bot.run(TOKEN)