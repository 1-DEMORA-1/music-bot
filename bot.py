import discord
from discord.ext import commands
import asyncio
import yt_dlp
from async_timeout import timeout

# Настройки yt-dlp
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            data = data['entries'][0]
            
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class MusicPlayer:
    def __init__(self, ctx):
        self.bot = ctx.bot
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.cog = ctx.cog
        
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        
        self.current = None
        self.volume = 0.5
        self.now_playing = None
        
        ctx.bot.loop.create_task(self.player_loop())
        
    async def player_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            self.next.clear()
            
            try:
                async with timeout(300): 
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                if self.guild.voice_client:
                    await self.channel.send("Музыка не проигрывалась 5 минут, отключаюсь...")
                    return await self.destroy(self.guild)
                return
            
            if not self.guild.voice_client:
                return
            
            source.volume = self.volume
            self.current = source
            
            self.guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            embed = discord.Embed(title="Сейчас играет", description=f"**{source.title}**", color=discord.Color.green())
            embed.add_field(name="Длительность", value=f"{int(source.duration // 60)}:{int(source.duration % 60):02d}" if source.duration else "Неизвестно")
            self.now_playing = await self.channel.send(embed=embed)
            
            await self.next.wait()
            
            if self.now_playing:
                await self.now_playing.delete()
            self.current = None
    
    async def destroy(self, guild):
        return self.bot.loop.create_task(self.cog.cleanup(guild))

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}
        
    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass
            
        try:
            del self.players[guild.id]
        except KeyError:
            pass
    
    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player
            
        return player
    
    @commands.command(name='join', aliases=['j'])
    async def join(self, ctx):
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            if ctx.voice_client:
                await ctx.voice_client.move_to(channel)
            else:
                await channel.connect()
            await ctx.send(f"Подключился к каналу: {channel.name}")
        else:
            await ctx.send("Вы не находитесь в голосовом канале.")
    
    @commands.command(name='play', aliases=['p'])
    async def play(self, ctx, *, url):
        if not ctx.voice_client:
            await ctx.invoke(self.join)
            
        async with ctx.typing():
            try:
                player = self.get_player(ctx)
                source = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
                await player.queue.put(source)
                
                await ctx.send(f'Добавлено в очередь: **{source.title}**')
            except Exception as e:
                await ctx.send(f'Произошла ошибка: {str(e)}')
    
    @commands.command(name='volume', aliases=['vol'])
    async def volume(self, ctx, volume: int):
        if not ctx.voice_client:
            return await ctx.send("Я не подключен к голосовому каналу.")
            
        if 0 > volume > 100:
            return await ctx.send("Громкость должна быть от 0 до 100.")
            
        player = self.get_player(ctx)
        
        if ctx.voice_client.source:
            ctx.voice_client.source.volume = volume / 100
            
        player.volume = volume / 100
        await ctx.send(f"Громкость установлена на {volume}%")
    
    @commands.command(name='stop')
    async def stop(self, ctx):
        if not ctx.voice_client:
            return await ctx.send("Я не подключен к голосовому каналу.")
            
        await self.cleanup(ctx.guild)
        await ctx.send("Музыка остановлена и очередь очищена.")
    
    @commands.command(name='skip', aliases=['s'])
    async def skip(self, ctx):
        if not ctx.voice_client:
            return await ctx.send("Я не подключен к голосовому каналу.")
            
        if not ctx.voice_client.is_playing():
            return await ctx.send("Сейчас ничего не играет.")
            
        ctx.voice_client.stop()
        await ctx.send("Трек пропущен.")
    
    @commands.command(name='queue', aliases=['q'])
    async def queue_info(self, ctx):
        player = self.get_player(ctx)
        
        if player.queue.empty():
            return await ctx.send("Очередь пуста.")
            
        upcoming = list(player.queue._queue)
        
        embed = discord.Embed(title="Очередь", color=discord.Color.blue())
        
        if player.current:
            embed.add_field(name="Сейчас играет", value=player.current.title, inline=False)
        
        for i, track in enumerate(upcoming[:10], 1):
            embed.add_field(name=f"{i}. {track.title}", value=f"Длительность: {int(track.duration // 60)}:{int(track.duration % 60):02d}" if track.duration else "Неизвестно", inline=False)
        
        if len(upcoming) > 10:
            embed.set_footer(text=f"И еще {len(upcoming) - 10} треков...")
            
        await ctx.send(embed=embed)
    
    @commands.command(name='pause')
    async def pause(self, ctx):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("Воспроизведение приостановлено.")
        else:
            await ctx.send("Сейчас ничего не играет.")
    
    @commands.command(name='resume')
    async def resume(self, ctx):
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("Воспроизведение возобновлено.")
        else:
            await ctx.send("Воспроизведение не приостановлено.")
    
    @commands.command(name='leave', aliases=['disconnect', 'dc'])
    async def leave(self, ctx):
        """Отключение от голосового канала"""
        if not ctx.voice_client:
            return await ctx.send("Я не подключен к голосовому каналу.")
            
        await self.cleanup(ctx.guild)
        await ctx.send("Отключился от голосового канала.")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id == self.bot.user.id and before.channel and not after.channel:
            guild = before.channel.guild
            player = self.players.get(guild.id)
            
            if player:
                self.bot.loop.create_task(self.cleanup(guild))
        
        elif before.channel and member.id != self.bot.user.id:
            voice = member.guild.voice_client
            if voice and voice.channel == before.channel:
                if len(voice.channel.members) == 1:
                    await asyncio.sleep(120)
                    if voice and len(voice.channel.members) == 1:
                        await self.cleanup(member.guild)
                        text_channel = self.players.get(member.guild.id).channel if member.guild.id in self.players else None
                        if text_channel:
                            await text_channel.send("Покидаю голосовой канал, так как остался один.")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Бот {bot.user.name} готов к работе!')
    
async def setup(bot):
    await bot.add_cog(Music(bot))
    

@bot.event
async def on_ready():
    print(f'Бот {bot.user.name} готов к работе!')
    await setup(bot)

bot.run('токен')
