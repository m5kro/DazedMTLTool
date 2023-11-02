from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sys
import textwrap
import threading
import time
import traceback
import tiktoken

from colorama import Fore
from dotenv import load_dotenv
import openai
from retry import retry
from tqdm import tqdm

#Globals
load_dotenv()
openai.organization = os.getenv('org')
openai.api_key = os.getenv('key')

APICOST = .002 # Depends on the model https://openai.com/pricing
PROMPT = Path('prompt.txt').read_text(encoding='utf-8')
THREADS = 20
LOCK = threading.Lock()
WIDTH = 75
LISTWIDTH = 75
MAXHISTORY = 10
ESTIMATE = ''
TOTALCOST = 0
TOKENS = 0
TOTALTOKENS = 0

#tqdm Globals
BAR_FORMAT='{l_bar}{bar:10}{r_bar}{bar:-10b}'
POSITION=0
LEAVE=False

# Flags
CODE401 = True
CODE102 = True
CODE122 = False
CODE101 = False
CODE355655 = False
CODE357 = False
CODE356 = False
CODE320 = False
CODE111 = False

def handleLune(filename, estimate):
    global ESTIMATE, TOKENS, TOTALTOKENS, TOTALCOST
    ESTIMATE = estimate

    if estimate:
        start = time.time()
        translatedData = openFiles(filename)

        # Print Result
        end = time.time()
        tqdm.write(getResultString(translatedData, end - start, filename))
        with LOCK:
            TOTALCOST += translatedData[1] * .001 * APICOST
            TOTALTOKENS += translatedData[1]
    
    else:
        with open('translated/' + filename, 'w', encoding='shiftjis') as outFile:
            start = time.time()
            translatedData = openFiles(filename)

            # Print Result
            end = time.time()
            outFile.writelines(translatedData[0])
            tqdm.write(getResultString(translatedData, end - start, filename))
            with LOCK:
                TOTALCOST += translatedData[1] * .001 * APICOST
                TOTALTOKENS += translatedData[1]

    return getResultString(['', TOTALTOKENS, None], end - start, 'TOTAL')

def openFiles(filename):
    with open('files/' + filename, 'r', encoding='shiftjis') as f:
        translatedData = parseText(f, filename)
    
    return translatedData

def getResultString(translatedData, translationTime, filename):
    # File Print String
    tokenString = Fore.YELLOW + '[' + str(translatedData[1]) + \
        ' Tokens/${:,.4f}'.format(translatedData[1] * .001 * APICOST) + ']'
    timeString = Fore.BLUE + '[' + str(round(translationTime, 1)) + 's]'

    if translatedData[2] == None:
        # Success
        return filename + ': ' + tokenString + timeString + Fore.GREEN + u' \u2713 ' + Fore.RESET

    else:
        # Fail
        try:
            raise translatedData[2]
        except Exception as e:
            errorString = str(e) + Fore.RED
            return filename + ': ' + tokenString + timeString + Fore.RED + u' \u2717 ' +\
                errorString + Fore.RESET
        
def parseText(data, filename):
    totalTokens = 0
    totalLines = 0
    global LOCK

    # Get total for progress bar
    linesList = data.readlines()
    totalLines = len(linesList)
    
    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
        pbar.desc=filename
        pbar.total=totalLines
        try:
            response = translateText(linesList, pbar)
        except Exception as e:
            traceback.print_exc()
            return [linesList, 0, e]
    return [response[0], response[1], None]

def translateText(data, pbar):
    textHistory = []
    maxHistory = MAXHISTORY
    tokens = 0
    speaker = ''
    speakerFlag = False
    currentGroup = []
    syncIndex = 0

    ### Translation
    for i in range(len(data)):
        if syncIndex > i:
            i = syncIndex

        # Finish if at end
        if i+1 > len(data):
            return [data, tokens]

        # Remove newlines
        jaString = data[i]
        jaString = jaString.replace('\n', '')

        # Reset Speaker
        if '00000000' == jaString:
            i += 1
            speaker = ''
            jaString = data[i]

        # Grab and Translate Speaker
        elif '00003000' == jaString or '00002000' == jaString:
            i += 1
            jaString = data[i].replace('\n', '')
            # Known Speakers
            namesList = [
            "John",
            "Bob",
            "Tom",
            "女教師"
            ]
            if jaString in namesList:
                jaString = jaString.replace('女教師', 'Female Teacher')
            
            # Translate Speaker
            response = translateGPT(jaString, 'Reply with only the english translation of the NPC name', True)
            tokens += response[1]
            speaker = response[0].strip('.')
            data[i] = speaker + '\n'

            # Set index to line
            i += 1

        else:
            continue
        
        # Translate
        finalJAString = data[i]
        if speaker != '':
            response = translateGPT(f'{speaker}: {finalJAString}', 'Previous Text for Context: ' + ' '.join(textHistory), True)
        else:
            response = translateGPT(finalJAString, 'Previous Text for Context: ' + ' '.join(textHistory), True)
        tokens += response[1]
        translatedText = response[0]
        
        # Remove added speaker and quotes
        translatedText = re.sub(r'^.+?:\s', '', translatedText)

        # TextHistory is what we use to give GPT Context, so thats appended here.
        if speaker != '':
            textHistory.append(speaker + ': ' + translatedText)
        elif speakerFlag == False:
            textHistory.append('\"' + translatedText + '\"')

        # Keep textHistory list at length maxHistory
        if len(textHistory) > maxHistory:
            textHistory.pop(0)
        currentGroup = []  

        # Textwrap
        translatedText = textwrap.fill(translatedText, width=40)
        translatedText = translatedText.replace('\n', '\\n')

        # Set Data
        data[i] = translatedText + '\n'
        syncIndex = i + 1
        pbar.update()
    return [data, tokens]
        
def subVars(jaString):
    jaString = jaString.replace('\u3000', ' ')

    # Icons
    count = 0
    iconList = re.findall(r'[\\]+[iIkKwW]+\[[0-9]+\]', jaString)
    iconList = set(iconList)
    if len(iconList) != 0:
        for icon in iconList:
            jaString = jaString.replace(icon, '[Ascii_' + str(count) + ']')
            count += 1

    # Colors
    count = 0
    colorList = re.findall(r'[\\]+[cC]\[[0-9]+\]', jaString)
    colorList = set(colorList)
    if len(colorList) != 0:
        for color in colorList:
            jaString = jaString.replace(color, '[Color_' + str(count) + ']')
            count += 1

    # Names
    count = 0
    nameList = re.findall(r'[\\]+[nN]\[.+?\]+', jaString)
    nameList = set(nameList)
    if len(nameList) != 0:
        for name in nameList:
            jaString = jaString.replace(name, '[N_' + str(count) + ']')
            count += 1

    # Variables
    count = 0
    varList = re.findall(r'[\\]+[vV]\[[0-9]+\]', jaString)
    varList = set(varList)
    if len(varList) != 0:
        for var in varList:
            jaString = jaString.replace(var, '[Var_' + str(count) + ']')
            count += 1

    # Formatting
    count = 0
    if '笑えるよね.' in jaString:
        print('t')
    formatList = re.findall(r'[\\]+CL', jaString)
    formatList = set(formatList)
    if len(formatList) != 0:
        for var in formatList:
            jaString = jaString.replace(var, '[FCode_' + str(count) + ']')
            count += 1

    # Put all lists in list and return
    allList = [iconList, colorList, nameList, varList, formatList]
    return [jaString, allList]

def resubVars(translatedText, allList):
    # Fix Spacing and ChatGPT Nonsense
    matchList = re.findall(r'\[\s?.+?\s?\]', translatedText)
    if len(matchList) > 0:
        for match in matchList:
            text = match.replace(' ', '')
            translatedText = translatedText.replace(match, text)

    # Icons
    count = 0
    if len(allList[0]) != 0:
        for var in allList[0]:
            translatedText = translatedText.replace('[Ascii_' + str(count) + ']', var)
            count += 1

    # Colors
    count = 0
    if len(allList[1]) != 0:
        for var in allList[1]:
            translatedText = translatedText.replace('[Color_' + str(count) + ']', var)
            count += 1

    # Names
    count = 0
    if len(allList[2]) != 0:
        for var in allList[2]:
            translatedText = translatedText.replace('[N_' + str(count) + ']', var)
            count += 1

    # Vars
    count = 0
    if len(allList[3]) != 0:
        for var in allList[3]:
            translatedText = translatedText.replace('[Var_' + str(count) + ']', var)
            count += 1
    
    # Formatting
    count = 0
    if len(allList[4]) != 0:
        for var in allList[4]:
            translatedText = translatedText.replace('[FCode_' + str(count) + ']', var)
            count += 1

    # Remove Color Variables Spaces
    # if '\\c' in translatedText:
    #     translatedText = re.sub(r'\s*(\\+c\[[1-9]+\])\s*', r' \1', translatedText)
    #     translatedText = re.sub(r'\s*(\\+c\[0+\])', r'\1', translatedText)
    return translatedText

@retry(exceptions=Exception, tries=5, delay=5)
def translateGPT(t, history, fullPromptFlag):
    # If ESTIMATE is True just count this as an execution and return.
    if ESTIMATE:
        enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
        tokens = len(enc.encode(t)) * 2 + len(enc.encode(history)) + len(enc.encode(PROMPT))
        return (t, tokens)
    
    # Sub Vars
    varResponse = subVars(t)
    subbedT = varResponse[0]

    # If there isn't any Japanese in the text just skip
    if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴ]+|[\uFF00-\uFFEF]', subbedT):
        return(t, 0)

    """Translate text using GPT"""
    context = '```\
        Game Characters:\
        Character: 如月亜里愛 == Kisaragi Aria - Nickname: Aria - Gender: Female\
        Character: 愛洲美彌子 == Aisu Miyako - Gender: Female\
        Character: 喜遊名心 == Cocoa Kiyuna - Gender: Female\
        Character: 柵瀬愛色 == Ai Sakurai - Gender: Female\
        Character: 陰平小鞠 == Komari Kagehira - Gender: Female\
        Character: 訓覇一縷 == Ichiru Kurube - Gender: Female\
        Character: 緋皇月 == Luna Hisube - Gender: Female\
        Character: 刑事 == Detective - Gender: Male\
        ```'

    if fullPromptFlag:
        system = PROMPT
        user = 'Line to Translate = ' + subbedT
    else:
        system = 'Output ONLY the english translation in the following format: `Translation: <ENGLISH_TRANSLATION>`' 
        user = 'Line to Translate = ' + subbedT
    response = openai.ChatCompletion.create(
        temperature=0,
        frequency_penalty=0.2,
        presence_penalty=0.2,
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": context},
            {"role": "user", "content": history},
            {"role": "user", "content": user}
        ],
        request_timeout=30,
    )

    # Save Translated Text
    translatedText = response.choices[0].message.content
    tokens = response.usage.total_tokens

    # Resub Vars
    translatedText = resubVars(translatedText, varResponse[1])

    # Remove Placeholder Text
    translatedText = translatedText.replace('English Translation: ', '')
    translatedText = translatedText.replace('Translation: ', '')
    translatedText = translatedText.replace('Line to Translate = ', '')
    translatedText = translatedText.replace('Translation = ', '')
    translatedText = translatedText.replace('Translate = ', '')
    translatedText = translatedText.replace('English Translation:', '')
    translatedText = translatedText.replace('Translation:', '')
    translatedText = translatedText.replace('Line to Translate =', '')
    translatedText = translatedText.replace('Translation =', '')
    translatedText = translatedText.replace('Translate =', '')
    translatedText = re.sub(r'\n\nPast Translated Text:.*', '', translatedText, 0, re.DOTALL)
    translatedText = re.sub(r'Note:.*', '', translatedText)
    translatedText = translatedText.replace('っ', '')

    # Return Translation
    if len(translatedText) > 15 * len(t) or "I'm sorry, but I'm unable to assist with that translation" in translatedText:
        raise Exception
    else:
        return [translatedText, tokens]