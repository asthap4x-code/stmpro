import numpy as np
import pandas as pd
from ta.trend import EMAIndicator

def analyze_stm_pvip(df):
    """
    ১. STM PVIP (ট্রেন্ড ফলো স্ট্র্যাটেজি):
    - আপট্রেন্ড: ৩ থেকে ১০টি গ্রিন ক্যান্ডেল একটানা আসার পর ১টি রেড ক্যান্ডেল আসলে পরের ক্যান্ডেল CALL (UP)।
    - ডাউনট্রেন্ড: ৩ থেকে ১০টি রেড ক্যান্ডেল একটানা আসার পর ১টি গ্রিন ক্যান্ডেল আসলে পরের ক্যান্ডেল PUT (DOWN)।
    """
    if len(df) < 15:
        return "NONE", 0

    closes = df['close'].values
    opens = df['open'].values
    
    # ক্যান্ডেল কালার নির্ধারণ (গ্রিন = 1, রেড = -1)
    colors = [1 if closes[i] > opens[i] else -1 for i in range(len(df))]
    
    # শেষ চলমান ক্যান্ডেল বাদ দিয়ে পূর্ববর্তী কমপ্লিটেড ক্যান্ডেলগুলো চেক করা
    recent_colors = colors[-12:-1]
    if len(recent_colors) < 4:
        return "NONE", 0

    # আপট্রেন্ড প্যাটার্ন চেক (একাধিক গ্রিন ক্যান্ডেলের পর ১টি রেড)
    if recent_colors[-1] == -1:  # শেষ কমপ্লিটেড ক্যান্ডেল রেড
        green_count = 0
        for c in reversed(recent_colors[:-1]):
            if c == 1:
                green_count += 1
            else:
                break
        if 3 <= green_count <= 10:
            return "CALL", 90

    # ডাউনট্রেন্ড প্যাটার্ন চেক (একাধিক রেড ক্যান্ডেলের পর ১টি গ্রিন)
    if recent_colors[-1] == 1:  # শেষ কমপ্লিটেড ক্যান্ডেল গ্রিন
        red_count = 0
        for c in reversed(recent_colors[:-1]):
            if c == -1:
                red_count += 1
            else:
                break
        if 3 <= red_count <= 10:
            return "PUT", 90

    return "NONE", 0

def analyze_stm_pvip2(df):
    """
    ২. STM PVIP 2 (EMA + হরাইজন্টাল লাইন স্ট্র্যাটেজি):
    - EMA 10 ফিল্টার।
    - হরাইজন্টাল লাইন লেভেল (একটি ক্যান্ডেলের close এবং পরের ক্যান্ডেলের open একই প্রাইস)।
    - বডি সাইজ নিয়ম: ৫৫% থেকে ৮৫% অংশ লাইনের উপরে বা নিচে থাকা।
    """
    if len(df) < 25:
        return "NONE", 0

    close = df['close']
    open_p = df['open']
    high = df['high']
    low = df['low']

    # EMA 10 হিসাব
    ema_10 = EMAIndicator(close, window=10).ema_indicator()
    current_ema = ema_10.iloc[-1]
    
    curr_close = close.iloc[-1]
    curr_open = open_p.iloc[-1]
    curr_high = high.iloc[-1]
    curr_low = low.iloc[-1]
    
    body_size = abs(curr_close - curr_open)
    if body_size == 0:
        return "NONE", 0

    # ক্যান্ডেলগুলো EMA এর উপরে নাকি নিচে তা যাচাই
    is_above_ema = curr_close > current_ema and close.iloc[-2] > ema_10.iloc[-2]
    is_below_ema = curr_close < current_ema and close.iloc[-2] < ema_10.iloc[-2]

    # হরাইজন্টাল লাইন লেভেল খোঁজা (যেখানে একটির close এবং পরেরটির open সমান)
    target_level = 0
    level_found = False
    for i in range(len(df) - 5, len(df) - 1):
        if abs(close.iloc[i-1] - open_p.iloc[i]) < 0.00005:
            target_level = close.iloc[i-1]
            level_found = True
            break

    # যদি হরাইজন্টাল এক্সাক্ট প্রাইস না পাওয়া যায়, তবে সুইং হাই/লো ব্যবহার করবে
    if not level_found:
        target_level = high.iloc[-5:-1].mean() if is_above_ema else low.iloc[-5:-1].mean()

    # ক্যান্ডেল লেভেলে রিটার্ন বা টাচ করলে ৫৫-৮৫% বডি রুল চেক
    if is_above_ema and curr_low <= target_level <= curr_high:
        if curr_close > curr_open:  # গ্রিন ক্যান্ডেল
            above_portion = curr_close - target_level
            percentage = (above_portion / body_size) * 100
            if 55 <= percentage <= 85:
                return "CALL", 92

    elif is_below_ema and curr_low <= target_level <= curr_high:
        if curr_close < curr_open:  # রেড ক্যান্ডেল
            below_portion = target_level - curr_close
            percentage = (below_portion / body_size) * 100
            if 55 <= percentage <= 85:
                return "PUT", 92

    return "NONE", 0

def analyze_stm_strategy(df):
    """
    মাস্টার STM স্ট্র্যাটেজি যা আপনার দেওয়া PVIP এবং PVIP 2 উভয় লজিক চেক করে সিগন্যাল রিটার্ন করবে।
    """
    # প্রথমে PVIP ট্রেন্ড ফলো স্ট্র্যাটেজি চেক করবে
    signal, conf = analyze_stm_pvip(df)
    if signal != "NONE":
        return signal, conf
        
    # না মিললে PVIP 2 স্ট্র্যাটেজি চেক করবে
    signal, conf = analyze_stm_pvip2(df)
    return signal, conf
