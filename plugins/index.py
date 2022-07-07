import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import ChannelInvalid, ChatAdminRequired, UsernameInvalid, UsernameNotModified
from info import ADMINS
from info import INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
import re
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
lock = asyncio.Lock()


@Client.on_callback_query(filters.regex(r'^index'))
async def index_files(bot, query):
    if query.data.startswith('index_cancel'):
        temp.CANCEL = True
        return await query.answer("Arrêt de l’indexation😉")
    _, raju, chat, lst_msg_id, from_user = query.data.split("#")
    if raju == 'reject':
        await query.message.delete()
        await bot.send_message(int(from_user),
                               f'Votre soumission pour indexation {chat} a été refusé par nos modérateurs.\nContact Support @VisionsT',
                               reply_to_message_id=int(lst_msg_id))
        return

    if lock.locked():
        return await query.answer('Attendez, laissez-moi terminer le premier', show_alert=True)
    msg = query.message

    await query.answer('Traitement...⏳', show_alert=True)
    if int(from_user) not in ADMINS:
        await bot.send_message(int(from_user),
                               f'Votre soumission pour indexation {chat} a été accepté par nos modérateurs😀 et sera bientôt ajouté.✔️',
                               reply_to_message_id=int(lst_msg_id))
    await msg.edit(
        "Indexation démarrée🤖",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton('Annuler', callback_data='index_cancel')]]
        )
    )
    try:
        chat = int(chat)
    except:
        chat = chat
    await index_files_to_db(int(lst_msg_id), chat, msg, bot)


@Client.on_message((filters.forwarded | (filters.regex("(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")) & filters.text ) & filters.private & filters.incoming)
async def send_for_index(bot, message):
    if message.text:
        regex = re.compile("(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(message.text)
        if not match:
            return await message.reply('Lien non valide')
        chat_id = match.group(4)
        last_msg_id = int(match.group(5))
        if chat_id.isnumeric():
            chat_id  = int(("-100" + chat_id))
    elif message.forward_from_chat.type == 'channel':
        last_msg_id = message.forward_from_message_id
        chat_id = message.forward_from_chat.username or message.forward_from_chat.id
    else:
        return
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply('Il peut s’agir d’un canal / groupe privé😔. Faites de moi un administrateur là-bas pour indexer les fichiers.😀\nBesoins de fichiers Contactez VisionTeam Support\n@VisionsT')
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply('Lien spécifié non valide.')
    except Exception as e:
        logger.exception(e)
        return await message.reply(f'Errors - {e}')
    try:
        k = await bot.get_messages(chat_id, last_msg_id)
    except:
        return await message.reply('Assurez-vous que je suis un administrateur dans le canal, si le canal est privé')
    if k.empty:
        return await message.reply('Il peut s’agir d’un groupe et je ne suis pas un administrateur du groupe.🥲')

    if message.from_user.id in ADMINS:
        buttons = [
            [
                InlineKeyboardButton('✔️APPROVED✔️',
                                     callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
            ],
            [
                InlineKeyboardButton('❌CANCEL❌', callback_data='close_data'),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        return await message.reply(
            f'Voulez-vous indexer ce canal ??\n\nChat ID/ Username: <code>{chat_id}</code>\nLast Message ID: <code>{last_msg_id}</code>',
            reply_markup=reply_markup)

    if type(chat_id) is int:
        try:
            link = (await bot.create_chat_invite_link(chat_id)).invite_link
        except ChatAdminRequired:
            return await message.reply('Assurez-vous que je suis un administrateur dans le chat et que vous avez l’autorisation d’inviter des utilisateurs.')
    else:
        link = f"@{message.forward_from_chat.username}"
    buttons = [
        [
            InlineKeyboardButton('ACCEPT INDEX✔️',
                                 callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
        ],
        [
            InlineKeyboardButton('REJECT INDEX❌',
                                 callback_data=f'index#reject#{chat_id}#{message.message_id}#{message.from_user.id}'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await bot.send_message(LOG_CHANNEL,
                           f'#IndexRequest\n\nBy : {message.from_user.mention} (<code>{message.from_user.id}</code>)\nChat ID/ Username - <code> {chat_id}</code>\nLast Message ID - <code>{last_msg_id}</code>\nInviteLink - {link}',
                           reply_markup=reply_markup)
    await message.reply('Merci pour la contribution😀, Attendre que mes modérateurs vérifient✅ les fichiers.\nFaite par @VisionsT')


@Client.on_message(filters.command('setskip') & filters.user(ADMINS))
async def set_skip_number(bot, message):
    if ' ' in message.text:
        _, skip = message.text.split(" ")
        try:
            skip = int(skip)
        except:
            return await message.reply("Le numéro de saut doit être un nombre entier.")
        await message.reply(f"Définissez avec succès le numéro de saut comme {skip}")
        temp.CURRENT = int(skip)
    else:
        await message.reply("Donnez-moi un numéro de saut")


async def index_files_to_db(lst_msg_id, chat, msg, bot):
    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0
    async with lock:
        try:
            current = temp.CURRENT
            temp.CANCEL = False
            async for message in bot.iter_messages(chat, lst_msg_id, temp.CURRENT):
                if temp.CANCEL:
                    await msg.edit(f"Annulé avec succès!!\n\nEnregistré <code>{total_files}</code> fichiers dans la base de données!\nFichiers en double ignorés: <code>{duplicate}</code>\nMessages ignorés supprimés: <code>{deleted}</code>\nMessages non multimédias ignorés: <code>{no_media + unsupported}</code>(Unsupported Media - `{unsupported}` )\nDes erreurs se sont produites: <code>{errors}</code>")
                    break
                current += 1
                if current % 20 == 0:
                    can = [[InlineKeyboardButton('Cancel', callback_data='index_cancel')]]
                    reply = InlineKeyboardMarkup(can)
                    await msg.edit_text(
                        text=f"Nombre total de messages récupérés: <code>{current}</code>\nNombre total de messages enregistrés: <code>{total_files}</code>\nFichiers en double ignorés: <code>{duplicate}</code>\nFichiers en double ignorés: <code>{deleted}</code>\nMessages non multimédias ignorés: <code>{no_media + unsupported}</code>(Unsupported Media - `{unsupported}` )\nDes erreurs se sont produites: <code>{errors}</code>",
                        reply_markup=reply)
                if message.empty:
                    deleted += 1
                    continue
                elif not message.media:
                    no_media += 1
                    continue
                elif message.media not in ['audio', 'video', 'document']:
                    unsupported += 1
                    continue
                media = getattr(message, message.media, None)
                if not media:
                    unsupported += 1
                    continue
                media.file_type = message.media
                media.caption = message.caption
                aynav, vnay = await save_file(media)
                if aynav:
                    total_files += 1
                elif vnay == 0:
                    duplicate += 1
                elif vnay == 2:
                    errors += 1
        except Exception as e:
            logger.exception(e)
            await msg.edit(f'Error: {e}')
        else:
            await msg.edit(f'Enregistré avec succès <code>{total_files}</code> vers la base de données!\nFichiers en double ignorés: <code>{duplicate}</code>\nMessages supprimés ignorés: <code>{deleted}</code>\nMessages non multimédias ignorés: <code>{no_media + unsupported}</code>(Unsupported Media - `{unsupported}` )\nDes erreurs se sont produites: <code>{errors}</code>')
