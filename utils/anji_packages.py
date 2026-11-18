"""Confirmed 2026 Anji packages. Amounts are per package, never per night."""
import json
import re
from datetime import date, timedelta

HOTELS = {
    '云山境': '安吉云上草原度假村·云山境度假酒店',
    '松鼠部落': '安吉云上草原度假村·松鼠部落酒店',
    '半山隐竹': '安吉云上草原度假村半山隐竹酒店',
    '1168星空天文': '安吉云上草原·1168星空天文酒店',
}
COMMON = '亲子DIY课堂；换装美拍赠3张电子照；云溪湖垂钓2小时（自带渔具）；入住期间无限次酒店及景区接驳车；健身房、管家服务、索道快速通道（快速通道不等于索道票）。'

def match_hotel(title):
    text = str(title or '')
    if any(alias in text for alias in ('1168', '星空天文', '山巅星迹', '云端围星季', '亲子星野')):
        return '1168星空天文'
    names = [name for name in HOTELS if name in text]
    return names[0] if len(names) == 1 else ''

def packages(hotel):
    if hotel == '1168星空天文':
        return [
            dict(name='云端围星季·野奢房2天1晚', campaign='9月错峰促销', nights=1, price=1599,
                 room='野奢房大床或双床', breakfast='热带雨林双人早餐1份',
                 dining='双人深夜食堂1份',
                 tickets='双人云上草原3日观光门票（含索道）；景区酒店双人专属VIP入园通道票；130元餐饮消费券套票',
                 benefits='尊享旅拍3张；晨暮伴游；免费健身房；酒店VIP快速索道通道（非门票）；星空俱乐部影院、桌游、台球及手工DIY体验'),
            dict(name='云端围星季·野奢房3天2晚', campaign='9月错峰促销', nights=2, price=2399,
                 room='野奢房大床或双床', breakfast='热带雨林早餐2份',
                 dining='双人高山小火锅1份；双人深夜食堂2份',
                 tickets='双人云上草原3日观光门票（含索道）；景区酒店双人专属VIP入园通道票；130元餐饮消费券套票',
                 benefits='尊享旅拍3张；晨暮伴游；免费健身房；酒店VIP快速索道通道（非门票）；星空俱乐部影院、桌游、台球及手工DIY体验'),
            dict(name='亲子赏秋游·家庭房2天1晚', campaign='9月错峰促销', nights=1, price=1899,
                 room='度假野奢套房/星球奇遇家庭房/苍穹观星大床房', breakfast='热带雨林双人早餐1份',
                 dining='双人高山小火锅1份；双人深夜食堂1份',
                 tickets='双人云上草原3日观光门票（含索道）；景区酒店双人专属VIP入园通道票；130元餐饮消费券套票',
                 benefits='尊享旅拍3张；晨暮伴游；免费健身房；酒店VIP快速索道通道（非门票）；星空俱乐部影院、桌游、台球及手工DIY体验'),
            dict(name='亲子赏秋游·家庭房3天2晚', campaign='9月错峰促销', nights=2, price=2599,
                 room='度假野奢套房/星球奇遇家庭房/苍穹观星大床房', breakfast='热带雨林早餐2份',
                 dining='双人高山小火锅1份；双人深夜食堂2份',
                 tickets='双人云上草原3日观光门票（含索道）；景区酒店双人专属VIP入园通道票；130元餐饮消费券套票',
                 benefits='尊享旅拍3张；晨暮伴游；免费健身房；酒店VIP快速索道通道（非门票）；星空俱乐部影院、桌游、台球及手工DIY体验'),
            dict(name='山巅星迹·星球房2天1晚', campaign='携程919大促', nights=1, price=1699,
                 room='星球房大床或双床（奇遇大、奇星妙语双）', breakfast='热带雨林早餐2份',
                 dining='深夜食堂2份',
                 tickets='双人云上草原2日观光大门票、双人往返索道票、非滑雪季双人云上草原2日2次滑雪票（熊猫部落探险乐园门票）',
                 benefits='尊享旅拍3张；晨暮伴游；仙侠向导伴游（周五至周日每天16:30开展）；景区游览图册；健身房和停车；酒店VIP通道；影院、桌游、台球及手工DIY体验'),
            dict(name='亲子星野·星球家庭房2天1晚', campaign='携程919大促', nights=1, price=1799,
                 room='星球家庭房（星球奇遇家庭房）', breakfast='热带雨林早餐2份',
                 dining='深夜食堂2份',
                 tickets='双人云上草原2日观光大门票、双人往返索道票、非滑雪季双人云上草原2日2次滑雪票（熊猫部落探险乐园门票）；景区酒店三人专属VIP入园通道票；130元餐饮消费券套票',
                 benefits='尊享旅拍3张；晨暮伴游；仙侠向导伴游（周五至周日每天16:30开展）；景区游览图册；健身房和停车；酒店VIP通道；影院、桌游、台球及手工DIY体验'),
            dict(name='云端两晚·星球房3天2晚', campaign='携程919大促', nights=2, price=2499,
                 room='星球主题房（奇遇大、奇星妙语双）', breakfast='热带雨林早餐4份',
                 dining='深夜食堂4份；星空徽章1份；伴手礼1份；130元餐饮消费券套票',
                 tickets='双人云上草原2日观光大门票、双人往返索道票、非滑雪季双人云上草原2日2次滑雪票（熊猫部落探险乐园门票）；景区酒店双人专属VIP入园通道票',
                 benefits='尊享旅拍3张；晨暮伴游；仙侠向导伴游（周五至周日每天16:30开展）；景区游览图册；健身房和停车；酒店VIP通道；影院、桌游、台球及手工DIY体验'),
        ]
    cloud = hotel == '云山境'
    rows = [
        dict(name='双人1晚＋熊猫＋大竹海', nights=1, price=499 if cloud else 699, room='大床或双床', breakfast='次日双人自助早餐', tickets='双人熊猫大门票、双人大竹海大门票'),
        dict(name='双人1晚＋云上草原', nights=1, price=1099 if cloud else 1299, room='大床或双床', breakfast='次日双人自助早餐', tickets='双人三日云上含索道'),
    ]
    if cloud:
        rows.append(dict(name='双人2晚＋云上草原', nights=2, price=1599, room='大床或双床', breakfast='两天双人自助早餐', tickets='双人三日云上含索道、双人温汤'))
    else:
        rows.extend([
            dict(name='3人1晚＋熊猫乐园', nights=1, price=999, room='家庭房', breakfast='次日3人自助早餐', tickets='3人熊猫大门票'),
            dict(name='2大1小1晚＋云上草原', nights=1, price=1899, room='家庭房', breakfast='次日3人自助早餐', tickets='2大1小三日云上含索道；儿童资格须转人工，不自行判断'),
        ])
    return rows

def total_price(hotel, package, check_in):
    if hotel == '1168星空天文':
        campaign = package.get('campaign')
        use_start = date(2026, 8, 31) if campaign == '9月错峰促销' else date(2026, 8, 20)
        use_end = date(2026, 12, 20) if campaign == '9月错峰促销' else date(2027, 4, 30)
        nights = [check_in + timedelta(days=i) for i in range(package['nights'])]
        if any(not use_start <= day <= use_end for day in nights):
            raise ValueError('入住日期超出使用期，转人工')
        extras = []
        for day in nights:
            tier = 0
            if campaign == '9月错峰促销':
                if day.month == 8 and day.weekday() in {4, 5}:
                    tier = 800
                if day.year == 2026 and day.month == 10 and 1 <= day.day <= 4:
                    tier = max(tier, 800)
                if day.year == 2026 and ((day.month == 9 and day.day in {25, 26}) or (day.month == 10 and day.day in {5, 6})):
                    tier = max(tier, 200)
            else:
                if day.weekday() in {4, 5} or (day.year == 2026 and day.month == 9 and day.day in {25, 26}):
                    tier = 200
                if day.year == 2026 and day.month == 10 and 1 <= day.day <= 4:
                    tier = max(tier, 600)
                if date(2026, 12, 20) <= day <= date(2027, 3, 15):
                    tier = max(tier, 1000 if day.weekday() in {4, 5} else 600)
                if (date(2027, 1, 1) <= day <= date(2027, 1, 3) or
                    date(2027, 1, 29) <= day <= date(2027, 2, 4) or
                    date(2027, 2, 12) <= day <= date(2027, 2, 20)):
                    tier = max(tier, 1200)
                if date(2027, 2, 5) <= day <= date(2027, 2, 11):
                    tier = max(tier, 1800)
            extras.append(tier)
        return package['price'] + max(extras or [0])
    nights = [check_in + timedelta(days=i) for i in range(package['nights'])]
    if any(not date(2026, 8, 25) <= day <= date(2026, 12, 31) for day in nights):
        raise ValueError('入住日期超出使用期，转人工')
    tiers = (100, 200, 500) if hotel == '云山境' else (200, 300, 600)
    extra = []
    for day in nights:
        tier = 0
        if day.weekday() == 5 or (day.month, day.day) in ((9, 30), (10, 6)):
            tier = tiers[0]
        if day.month == 9 and day.day in (25, 26):
            tier = max(tier, tiers[1])
        if day.month == 10 and 1 <= day.day <= 5:
            tier = max(tier, tiers[2])
        extra.append(tier)
    return package['price'] + max(extra)

def child_question(message):
    return bool(re.search(r'儿童|小孩|孩子|宝宝|小朋友|身高|几岁|\d+\s*岁|\d+(?:\.\d+)?\s*米|儿童标准', message))

def knowledge(hotel, check_in=None):
    rows = packages(hotel)
    if check_in:
        rows = [dict(row, date_total=total_price(hotel, row, check_in)) for row in rows]
    if hotel == '1168星空天文':
        sale = '两组套餐均为2026-08-20至2026-09-30'
        use = '9月错峰促销：2026-08-31至2026-12-20；携程919大促：2026-08-20至2027-04-30'
        surcharge = ('每套仅取实际入住各晚最高一档，不叠加、不乘晚数，退房日不计。'
                     '9月错峰促销：8月周五周六+800，国庆10月1-4日+800，中秋9月25-26日及10月5-6日+200。'
                     '携程919大促：中秋及周五周六+200，国庆10月1-4日+600，12月20日至3月15日周内+600、周末+1000，'
                     '元旦1月1-3日、1月29日至2月4日、2月12-20日+1200，春节2月5-11日+1800。')
    else:
        sale = '2026-08-25至2026-09-19'
        use = '2026-08-25至2026-12-31'
        surcharge = '每套仅取实际入住各晚最高一档，不叠加、不乘晚数，退房日不计。云山境周六/9月30日/10月6日+100，中秋9月25-26日+200，国庆10月1-5日+500；松鼠部落/半山隐竹对应+200/+300/+600。国庆截图库存限20间，不代表实时库存。'
    return json.dumps(dict(
        hotel=HOTELS[hotel], packages=rows, common=COMMON,
        sale=sale, use=use, surcharge=surcharge,
        instructions='独立按各套餐权益回答，不混用。date_total为系统已算好的整套总价，直接使用，不转换每晚、不再加价。未确定日期只询问入住日期；未指定套餐列出对应酒店可选套餐，不能套用乌镇普通房型规则。询问儿童资格、实际预约和房态转人工，禁止占位回复。',
    ), ensure_ascii=False)
