import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import calendar
from OMA_tools.io_data.dates import Dates_Operations


class Constants:
    def __init__(self, dict_data = None):
        """
        Конструктор класса Constants.
        
        Args:
            dict_data: словарь с данными (опционально)
        """
        self.dict_data = dict_data
        
        
    @property
    def company_name_dict(self):
        """
            Список Каналов. Каждое значение из данного списка соответствует значению из спика basedemo_filter_list (список с БЦА)
        """
        companies = [
            #Первый,Россия 1, НТВ, РЕН ТВ, Пятый канал, Домашний, ТВ-3, Россия 24, ТВ Центр, Звезда, Локальное ТВ
            'tvNetId IN (1, 2, 4, 60, 259, 257, 206, 260, 12, 258, 300)',
            'tvNetId IN (1)',       # Первый
            'tvNetId IN (11)',      # СТС
            'tvNetId IN (83, 204, 300)', # ТНТ, Пятница
            'tvNetId IN (83, 204)', # ТНТ, Пятница
            'tvNetId IN (255)',     # ЧЕ
            'tvNetId IN (206)',     # ТВ-3
            'tvNetId IN (40)',      # Карусель
            'tvNetId IN (11)',      # СТС
            'tvNetId IN (205)',     # Ю
            'tvNetId IN (257)'      # Домашний
        ]
        return dict(zip(self.bca_list_names, companies))
    
    
    @property
    def basedemo_filter_dict(self):
        """
            Список названий БЦА
        """
        basedemos = [
            'age >= 18', # Первый,Россия 1, НТВ, РЕН ТВ, Пятый канал, Домашний, ТВ-3, Россия 24, ТВ Центр, Звезда
            'age >= 14 AND age <= 59',              # Первый
            'age >= 10 AND age <= 45',              # СТС
            'age >= 14 AND age <= 44',              # ТНТ, Пятница
            'age >= 14 AND age <= 54',              # ТНТ, Пятница
            'age >= 25 AND age <= 49',              # ЧЕ
            'age >= 25 AND age <= 54',              # ТВ-3
            'age >= 4 AND age <= 45',               # Карусель
            'age >= 6 AND age <= 54',               # СТС
            'age >= 14 AND age <= 44 AND sex = 2',  # Ю
            'age >= 25 AND age <= 59 AND sex = 2'   # Домашний
        ]
        return dict(zip(self.bca_list_names, basedemos))
    
    
    @property
    def regions_dict_list(self):
        """
            Список городов и их ID
        """
        if self.dict_data is None:
            raise ValueError("dict_data не инициализирован. Передайте словарь при создании объекта Constants.")
        else:
            return [self.dict_data['All 18+'], self.dict_data['All 14-59'], self.dict_data['All 10-45'], 
                    self.dict_data['All 14-44'], self.dict_data['All 14-54'], self.dict_data['All 25-49'], self.dict_data['All 25-54'], 
                    self.dict_data['All 4-45'], self.dict_data['All 6-54'], self.dict_data['W 14-44'], self.dict_data['W 25-59']]
    
    
    @property
    def bca_list_names(self):
        """
            Список БЦА
        """
        return ['All 18+', 'All 14-59', 'All 10-45', 'All 14-44', 'All 14-54', 'All 25-49', 'All 25-54', 'All 4-45', 'All 6-54', 'W 14-44', 'W 25-59']
    

    @property
    def gen_date_filter_periods(self):
        """
            Метод для автоматической генерации периодов для выгрузок
        """
        current_date = datetime.now()
        START_OF_CURR_YEAR = f'{current_date.year}-01-01'
        STOP_OF_CURR_YEAR = f'{current_date.year}-12-31'

        def get_previous_month_date(months_ago=1):
            """
                Вспомогательная функция для вычисления прошлого месяца
            """
            target_date = datetime.now() - relativedelta(months=months_ago)
            return target_date.year, target_date.month

        today = date.today()
        local_time = datetime.now()
        
        # Инициализация всех переменных значениями по умолчанию
        DATE_FILTER_LAST_14_DAYS = None
        DATE_FILTER_LAST_21_DAYS = None
        DATE_FILTER_PREV_14 = None
        DATE_FILTER_BY_DATES = None
        DATE_FILTER_FULL_MONTH = None
        DATE_FILTER_FACT_MONTH = None
        DATE_FILTER_PREV_TO_FACT_MONTH = None

        ################## ГЕНЕРАЦИЯ ПЕРИОДОВ: НЕДЕЛЬНЫЕ И 50 ДНЕЙ ##################
        if local_time.hour < 12:                                                                              # ПОМЕНЯТЬ НА 12
         # ПОСЛЕДНИЕ 2 НЕДЕЛИ
            DATE_FILTER_LAST_14_DAYS = Dates_Operations(number_of_previous_days=[-16, -3]).date_filter
            
            # ПОСЛЕДНИЕ 3 НЕДЕЛИ
            DATE_FILTER_LAST_21_DAYS = Dates_Operations(number_of_previous_days=[-23, -3]).date_filter
            
            # ДВЕ НЕДЕЛИ ПЕРЕД ПОСЛЕДНИМИ ДВУМЯ НЕДЕЛЯМИ
            DATE_FILTER_PREV_14 = Dates_Operations(number_of_previous_days=[-30, -17]).date_filter
            
            # ПОСЛЕДНИЕ 50 ДНЕЙ
            date_start = datetime.now() + timedelta(days = -51)
            start_date = date_start.strftime('%Y-%m-%d')
            date_stop = datetime.now() + timedelta(days = -3)
            stop_date = date_stop.strftime('%Y-%m-%d')
            DATE_FILTER_BY_DATES = [(start_date, stop_date)]
        else:
            # ПОСЛЕДНИЕ 2 НЕДЕЛИ
            DATE_FILTER_LAST_14_DAYS = Dates_Operations(number_of_previous_days=[-15, -2]).date_filter # ПОМЕНЯТЬ НА -15, -2, (-18, -5)
            
            # ПОСЛЕДНИЕ 3 НЕДЕЛИ
            DATE_FILTER_LAST_21_DAYS = Dates_Operations(number_of_previous_days=[-22, -2]).date_filter # ПОМЕНЯТЬ НА -22, -2, (-25, -5)
            
            # ДВЕ НЕДЕЛИ ПЕРЕД ПОСЛЕДНИМИ ДВУМЯ НЕДЕЛЯМИ
            DATE_FILTER_PREV_14 = Dates_Operations(number_of_previous_days=[-29, -16]).date_filter # ПОМЕНЯТЬ НА -29, -16, (-32, -19)
            
            # ПОСЛЕДНИЕ 50 ДНЕЙ
            date_start = datetime.now() + timedelta(days = -55)  # ПОМЕНЯТЬ НА -50, (-53)
            start_date = date_start.strftime('%Y-%m-%d')
            date_stop = datetime.now() + timedelta(days = -2)   # ПОМЕНЯТЬ НА -2, (-5)
            stop_date = date_stop.strftime('%Y-%m-%d')
            DATE_FILTER_BY_DATES = [(start_date, stop_date)]

        ################## ГЕНЕРАЦИЯ ПЕРИОДОВ: МЕСЯЧНЫЕ ##################
        # Для всех случаев генерируем полный прошлый месяц
        prev_year, prev_month = get_previous_month_date(1)
        start_of_prev_month = f'{prev_year}-{prev_month:02d}-01'
        last_day_prev_month = calendar.monthrange(prev_year, prev_month)[1]
        stop_of_prev_month = f'{prev_year}-{prev_month:02d}-{last_day_prev_month}'
        DATE_FILTER_FULL_MONTH = [(start_of_prev_month, stop_of_prev_month)]
        
        # Генерация начала текущего месяца
        curr_month = current_date.month
        start_of_curr_month = f'{current_date.year}-{curr_month:02d}-01'
        
        # Определяем смещение для last_fact_date в зависимости от часа
        days_offset = -3 if local_time.hour < 12 else -2  #РАССКОМЕНТИРОВАТЬ
        #days_offset = -5                                                                 # УБРАТЬ ЭТУ СТРОКУ
        # Генерация ФАКТА ТЕКУЩЕГО МЕСЯЦА
        date_stop = datetime.now() + timedelta(days=days_offset)
        last_fact_date = date_stop.strftime('%Y-%m-%d')
        DATE_FILTER_FACT_MONTH = [(start_of_curr_month, last_fact_date)]
        
        # Генерация периода ОТ 1ГО ЧИСЛА ПРОШЛОГО МЕСЯЦА ДО ФАКТА ТЕКУЩЕГО МЕСЯЦА
        DATE_FILTER_PREV_TO_FACT_MONTH = [(start_of_prev_month, last_fact_date)]
        
        # Специальные случаи для января и первых чисел месяца
        if today.day <= 15:
            if today.month == 1:
                # Для января: прошлый месяц (декабрь) закрывается позже из-за праздников
                # Переопределяем DATE_FILTER_FULL_MONTH
                year, month = get_previous_month_date(1)
                start_of_prev_month = f'{year}-{month:02d}-01'
                last_day_prev_month = calendar.monthrange(year, month)[1]
                stop_of_prev_month = f'{year}-{month:02d}-{last_day_prev_month}'
                DATE_FILTER_FULL_MONTH = [(start_of_prev_month, stop_of_prev_month)]
                
                # Факт текущего месяца уже сгенерирован выше, оставляем как есть
                
                # Период от прошлого месяца до факта уже сгенерирован выше
            
            elif today.day <= 10:
                # Для дней 1-10 не-января: подменяем факт месяца на полный прошлый месяц
                DATE_FILTER_FACT_MONTH = DATE_FILTER_FULL_MONTH.copy()
                
                # Для периода от 1го числа 2 месяца назад до конца прошлого месяца
                two_months_ago_year, two_months_ago_month = get_previous_month_date(2)
                start_of_2_months_ago = f'{two_months_ago_year}-{two_months_ago_month:02d}-01'
                DATE_FILTER_PREV_TO_FACT_MONTH = [(start_of_2_months_ago, stop_of_prev_month)]
            
            # Для дней 11-15 не-января ничего не переопределяем, 
            # все уже корректно сгенерировано в базовых настройках
        
        # Для дней >15 ничего дополнительно не делаем, базовые настройки уже корректны
        
        return {
            'full_month': DATE_FILTER_FULL_MONTH,
            'fact_month': DATE_FILTER_FACT_MONTH,
            'last_14_days': DATE_FILTER_LAST_14_DAYS,
            'last_21_days': DATE_FILTER_LAST_21_DAYS,
            'prev_14_before_last_14_days': DATE_FILTER_PREV_14,
            'prev_to_fact_month': DATE_FILTER_PREV_TO_FACT_MONTH,
            'by_dates': DATE_FILTER_BY_DATES,
            'start_of_year': START_OF_CURR_YEAR,
            'stop_of_year': STOP_OF_CURR_YEAR
        }
    

    @property
    def ttv_end_day_forecast(self):
        """
            Метод для генерации последней даты предсказания TTV. Если сегодняшнее число > 10, то меняем дату предсказания на конец следующего месяца. В противном случае оставляем дату конца текущего месяца
        """
        current_date = datetime.now()
        
        # Если сегодня число > 10, то меняем дату предсказания на последнюю дату следующего месяца
        if current_date.day > 10:
            # Если текущий месяц декабрь, то увеличиваем год на 1 единицу
            if current_date.month == 12:
                year = current_date.year + 1
                month = 1
            else:
                year = current_date.year
                month = current_date.month + 1

            # Генерация конца следующего месяца
            last_day_future_month = calendar.monthrange(year, month)[1]
            print(f'В качестве последней даты предсказания TTV была выбрана дата: {last_day_future_month}.{month:02d}.{year}')
            return f'{last_day_future_month}.{month:02d}.{year}'
        
        else:
            year = current_date.year
            month = current_date.month
            last_day_curr_month = calendar.monthrange(year, month)[1]
            print(f'В качестве последней даты предсказания TTV была выбрана дата: {last_day_curr_month}.{month:02d}.{year}')
            return f'{last_day_curr_month}.{month:02d}.{year}'