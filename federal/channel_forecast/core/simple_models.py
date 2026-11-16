import pandas as pd
import numpy as np
from collections import defaultdict
from itertools import combinations
import json
import calendar

from functools import lru_cache
import hashlib
from concurrent.futures import ProcessPoolExecutor

from OMA_tools.federal.channel_forecast.grid_preprocessing import ProgramMatcher
from OMA_tools.federal.channel_forecast.core.pipelines import *
from OMA_tools.federal.channel_forecast.core.content_matching import *
from OMA_tools.federal.channel_forecast.calculator import *
from OMA_tools.io_data.time_series import TimeSeriesTransformer
from OMA_tools.federal.channel_forecast.support import Assistant
from OMA_tools.io_data.colors import *


import locale
locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')

import warnings
warnings.filterwarnings('ignore')



class DataPreparator:
    """
        Класс для подготовки и отбора данных перед прогнозированием.
        Отвечает за фильтрацию, разделение по размерам, добавление признаков.
    """
    def __init__(
            self, 
            channel: str,
            palomars_df: pd.DataFrame,
            vimb_df: pd.DataFrame,
            vocabulary: pd.DataFrame
        ):
        self.channel = channel
        self.palomars_df = palomars_df
        self.vimb_df = vimb_df
        self.vocabulary = vocabulary

    
    def merge_programs_by_name(self, data_dict):
        """
            Объединяет датафреймы по названиям программ из всех дат
            
            Args:
                data_dict: словарь с структурой {дата: {программа: датафрейм}}
            
            Returns:
                словарь {название_программы: объединенный_датафрейм}
        """
        # Создаем словарь для накопления датафреймов по программам
        programs_collection = defaultdict(list)
        
        # Проходим по всем датам
        for date, programs_dict in data_dict.items():
            # Проходим по всем программам в этой дате
            for program_name, df in programs_dict.items():
                # Добавляем датафрейм в список для этой программы
                programs_collection[program_name].append(df)
        
        # Объединяем датафреймы для каждой программы
        result_dict = {}
        for program_name, df_list in programs_collection.items():
            if df_list:  # Если есть датафреймы для этой программы
                # Объединяем все датафреймы
                combined_df = pd.concat(df_list, ignore_index=True)
                # Сортируем по дате (если нужно)
                combined_df = combined_df.sort_values('Дата')
                # Удаляем дубликаты (если данные могут повторяться)
                combined_df = combined_df.drop_duplicates()
                
                result_dict[program_name] = combined_df
        
        return result_dict
    

    def aggregate_vimb_daily(self, cities_loaded):
        """
            Метод для схлопывания программ VIMB для каждого дня. 
            Один из методов подготовки таблицы для прогнозирования.
            Это процедуру нудно делать, тк при прогнозировании мы опираемся на историческую смэтченную сетку Palomars и VIMB.
        """
        vimb_init_copy = self.vimb_df.copy()
        
        # Список из схлопнутых программ для каждого дня
        new_vimb = []
        
        dates_unique = vimb_init_copy['Дата'].unique()
        
        for date in dates_unique:
        
            table_vimb = vimb_init_copy[vimb_init_copy['Дата'] == date].reset_index(drop = True)

            # Заменяем значения начиная со второго
            for i in range(1, len(table_vimb)):
                
                # Корректируем время окончания предыдущей программы
                if table_vimb.loc[i - 1, 'Время окончания'] != table_vimb.loc[i, 'Время выхода']:
                    #result.loc[i - 1, 'Время окончания'] = result.loc[i, 'Время выхода']
                    table_vimb.loc[i, 'Время выхода'] = table_vimb.loc[i - 1, 'Время окончания']

            VIMB = pd.DataFrame()

            #Программы в VIMB
            if self.channel == 'МатчТВ':
                sport_cleaner = SportChannelCleaner(self.channel)
                # 1. Программы в VIMB
                _, VIMB = sport_cleaner.clean_dataframe(table_vimb, 'vimb', cities_loaded)

            elif self.channel == 'МузТВ':
                music_cleaner = MusicChannelCleaner(self.channel)
                _, VIMB = music_cleaner.clean_dataframe(table_vimb)

            else:
                vimb_prepr = GeneralTextCleaner(self.channel)
                _, VIMB = vimb_prepr.clean_dataframe(table_vimb)
            
            VIMB = VIMB[['Дата', 'Название программы', 'program_name', 'Время выхода', 'Время окончания']]
            
            if self.channel != 'МатчТВ':
                # Находим базовые названия программ. Производим замену
                base_names = ProgramMatcher.find_common_base_names(VIMB['program_name'].tolist())
                
                VIMB['Базовое_название'] = VIMB['program_name'].map(base_names)
            else:
                VIMB.rename(columns = {'program_name': 'Базовое_название'}, inplace = True)
            
            # Отсавляем только нужные столбцы для анализа
            VIMB = VIMB[['Дата', 'Базовое_название', 'Время выхода', 'Время окончания', 'Название программы']]
            VIMB.rename(columns = {
                'Название программы': 'Название программы vimb',
                'Базовое_название': 'Название программы'
            }, inplace = True)

            # =============== Замена названий мультфильмов, связанных с Машей ===============
            assistant = Assistant()
            VIMB = assistant.check_cartoons_masha_and_bear(
                                        df = VIMB,
                                        target_name_cartoons = ['маша и медведь'],
                                        full_list = ['машины сказки', 'машины песенки', 'машины страшилки', 'маша и медведь', 'машкины страшилки'],
                                        replacement_name = 'мультфильм о маше'
                                        )
            # Замена названий остальных мультфильмов
            VIMB = assistant.replace_cartoons(df = VIMB, column = 'Название программы')
            # =========================================================================================================================
            
            # Схлопывание программ по дню
            tv_processor = TVScheduleProcessor(self.channel, VIMB, self.palomars_df)
            vimb_joined = tv_processor.join_broadcasts(VIMB, 'vimb', include_share = False)
    
            new_vimb.append(vimb_joined)
        
        # Соединяем все в единый датафрейм
        data = pd.concat(new_vimb)
        
        # Делаем верную сортировку по дате и времени выхода программы
        data['Дата'] = pd.to_datetime(data['Дата'])
        sorted_webs = data.sort_values('Дата').reset_index(drop = True)
        
        res = []
        for date in data['Дата'].unique():
            
            date_dt = pd.to_datetime(date)
            t = sorted_webs[sorted_webs['Дата'] == date_dt]
            t['sort_key'] = t['Время выхода'].apply(BaseParser.get_sort_key)
            final = t.sort_values('sort_key').reset_index(drop = True)
            final = final.drop('sort_key', axis = 1)
            
            res.append(final)
        
        # Итог со схлопывавнием программ в оригинальной сетке
        self.vimb_analysis = pd.concat(res).reset_index(drop = True)
        self.vimb_analysis['Дата'] = self.vimb_analysis['Дата'].dt.strftime('%Y-%m-%d')
        return self.vimb_analysis

    

    def find_and_categorize_programs(self, data_new: pd.DataFrame, data_hist: pd.DataFrame, year: int, month_num: int):
        """
            Поиск схожих программ в соответствии со схлопнутой исторической сеткой Mediascope, 
            а также составление больших, маленьких датафреймов. 
            Осуществляем поиск новых программ, которые раннее не встречались.

            Параметры:
            ----------
                data_new: pd.DataFrame
                    Таблица, для которой будем искать схожие программы (Таблица VIMB)
                data_hist: pd.DataFrame
                    Таблица, с помощью которой будем искать схожие программы (Таблица Palomars)
                year : int
                    Год, который будем отбирать. Отбирается весь текущий год, если прогнозируемый месяц не Январь. 
                    В противном случае весь прошлый год.
                month_num: int
                    Номер месяца, на который будем строить прогноз.
        """
        vimb_full = data_new.copy()

        grid_hist = data_hist.copy()

        # Если прогнозируемый месяц январь, то отбираем весь прошлый год. В противном случае весь текущий
        if month_num == 1:
            grid_hist = grid_hist[grid_hist['Дата'] >= f'{year - 1}-01-01'].reset_index(drop = True)
        else:
            grid_hist = grid_hist[grid_hist['Дата'] >= f'{year}-01-01'].reset_index(drop = True)

        palomars_prgms = list(set(grid_hist['program_name']))

        need_forecast = {}
        new_programs_dict = {}
        results = {}
        vimb_converted = {}

        # Отбираем уникальные даты в сетке VIMB
        dates_unique = self.vimb_analysis['Дата'].unique()

        for date in dates_unique:
            
            # Отбор конкретной даты в ВИМБ
            vimb = vimb_full[vimb_full['Дата'] == date].reset_index(drop = True)
            vimb_prgms = list(set(vimb['Название программы']))
            vimb.rename(columns = {'Название программы': 'program_name'}, inplace = True)
            
            # Делаем поиск по схожим программам
            similar = CosineSimilarity(self.channel, palomars_prgms, vimb_prgms, grid_hist, vimb)
            # Составление таблицей со схожестью (similarity)
            result, not_found, comparison = similar.comparison(self.vocabulary, min_similarity = 0.5, use_vocabulary = True)
            results[date] = result

            df = result[result['similarity'].round(5) != 0.00000]
            features_dict = similar.generate_similar_features(df, print_df = False)
            need_forecast[date] = features_dict

            new_ones = result[result['similarity'].round(5) == 0.00000]

            if len(new_ones) != 0:
                new_programs_dict[date] = new_ones
            
        # Программы, для которых нашлась история
        self.merged_dict = self.merge_programs_by_name(need_forecast)    
    
        return self.merged_dict, new_programs_dict
    

    def separate_programs_by_volume(self, window_size: int = 7):
        """
            Разделение программ на большие/маленькие датафреймы в зависимости от размера истории
        """
        small = {}
        big = {}
        new = {}
        not_found = pd.DataFrame()

        for program, data in self.merged_dict.items():
            
            data['Дата'] = pd.to_datetime(data['Дата'])
            data['День недели'] = data['Дата'].dt.strftime('%A')
            
            #Отбираем только ненулевые элементы
            #if program != 0:
            if program and str(program) != '0':
                if len(data) < 3 * window_size:
                    small[program] = data.reset_index(drop = True)
                else:
                    big[program] = data.reset_index(drop = True)

        # Для новых программ
        result = []
        
        vimb_copy = self.vimb_analysis.copy()

        if self.channel == 'МатчТВ':
            vimb_copy[['Вид спорта', 'Метка']] = vimb_copy.apply(
                lambda row: Assistant().process_row(row, column_with_initial_name = 'Название программы vimb'), 
                axis = 1
            )

        for date, df in self.new_programs.items():
            
            vimb_copy_ = vimb_copy.copy()
            
            # Отбираем новые программы для конкретного дня
            programs = list(df['Программа VIMB'])
            
            #vimb_copy_['Flag'] = vimb_copy_['Название программы'].str.contains('|'.join(programs))

            # Экранируем спецсимволы в названиях программ
            escaped_programs = [re.escape(prog) for prog in programs]
            pattern = '|'.join(escaped_programs)
            vimb_copy_['Flag'] = vimb_copy_['Название программы'].str.contains(pattern, case = False, na = False, regex = True)

            found = vimb_copy_[vimb_copy_['Flag'] == True].reset_index(drop = True)

            data = found[found['Дата'] == date]
            data_ = data.drop('Flag', axis = 1)
            result.append(data_)
        
        if len(result) != 0:
            not_found = pd.concat(result).reset_index(drop = True)

            # Добавляем столбец с днем недели
            not_found['Дата'] = pd.to_datetime(not_found['Дата'])
            not_found['День недели'] = not_found['Дата'].dt.strftime('%A')

            if self.channel == 'МатчТВ':
                not_found = not_found[['Дата', 'Название программы', 'Время выхода', 'Время окончания', 'День недели', 'Вид спорта', 'Метка']]
            else:
                not_found = not_found[['Дата', 'Название программы', 'Время выхода', 'Время окончания', 'День недели']]
            not_found['Share'] = ''

            programs = not_found['Название программы'].unique()
            for program in programs:
                df = not_found[not_found['Название программы'] == program].reset_index(drop = True)
                new[program] = df
        
        return small, big, new
    

    def prepare(self, year: int, month_num: int, cities_loaded):
        """
            Пайплайн для подготовки данных.

            Параметры:
            ----------
                year : int
                    Год, который будем отбирать. Отбирается весь текущий год, если прогнозируемый месяц не Январь. 
                    В противном случае весь прошлый год.
                month_num: int
                    Номер месяца, на который будем строить прогноз.
            
            Returns:
            ----------
                small: dict
                    Словарь из маленьких программ
                big: dict
                    Словарь из крупных программ
                not_found: pd.DataFrame
                    Таблица с ненайденными программами
        """
        # Шаг 1. Схлопываем программы для каждого дня в таблице VIMB
        self.vimb_analysis = self.aggregate_vimb_daily(cities_loaded)

        if self.channel == 'МатчТВ':
            print(f'Делаю особое разбиение программ на Спортивные и Неспортивные для канала {self.channel}.')

            # Шаг 2. Добавление дополнительных столбцов для канала "МатчТВ"
            self.vimb_analysis[['Вид спорта', 'Метка']] = self.vimb_analysis.apply(
                lambda row: Assistant().process_row(row, column_with_initial_name = 'Название программы vimb'), 
                axis = 1
            )

            # Отбор только спортивных трансляций
            sport_df = self.vimb_analysis[self.vimb_analysis['Вид спорта'] != ''].reset_index(drop = True)
            # Отбор спортивных трансляций в исторической сетке
            sport_mask = self.palomars_df['Вид спорта'].notna() & (self.palomars_df['Вид спорта'] != '')
            self.sport_palomars_df = self.palomars_df[sport_mask].reset_index(drop = True)

            # Отбор неспортивных трансляций
            not_sport = self.vimb_analysis[self.vimb_analysis['Вид спорта'] == ''].reset_index(drop = True)
            # Отбор НЕспортивных трансляций в исторической сетке
            self.not_sport_palomars_df = self.palomars_df[self.palomars_df['Вид спорта'].isna() | (self.palomars_df['Вид спорта'] == '')].reset_index(drop = True)

            # Шаг 3. Поиск схожих программ
            # ==== Поиск схожих программ для спортивных трансляций ====
            self.sport_merged_dict, sport_new_programs  = self.find_and_categorize_programs(
                                                            sport_df, self.sport_palomars_df, year, month_num
                                                                    )
            # ==== Поиск схожих программ для НЕспортивных трансляций ====
            self.not_sport_merged_dict, not_sport_new_programs = self.find_and_categorize_programs(
                                                            not_sport, self.not_sport_palomars_df, year, month_num
                                                                )
            self.merged_dict = self.sport_merged_dict | self.not_sport_merged_dict

            # Объединяем new_programs БЕЗ ПОТЕРЬ
            self.new_programs = {}
            for date, df in sport_new_programs.items():
                self.new_programs[date] = df

            for date, df in not_sport_new_programs.items():
                if date in self.new_programs:
                    self.new_programs[date] = pd.concat([self.new_programs[date], df], ignore_index=True)
                else:
                    self.new_programs[date] = df
        
        else:
            # Шаг 2. Поиск схожих программ
            self.merged_dict, self.new_programs = self.find_and_categorize_programs(self.vimb_analysis, self.palomars_df, year, month_num)

        # Разделение программ на большие, маленькие датафреймы, а также поиск новых программ
        small, big, not_found = self.separate_programs_by_volume()

        result = {
            'big': big,
            'small': small,
            'new': not_found
        }
        return result


#class RuleBasedForecaster:
#    """
#        Класс с реализацией простейшей модели для прогнозирования будущих программ.
#    """
#    def __init__(self, 
#                 channel: str,
#                 current_year: int,
#                 month_num: int,
#                 start_date_forecast: str,
#                 palomars_history: pd.DataFrame
#                ):
#        self.channel = channel
#        self.current_year = current_year
#        self.month_num = month_num
#        self.start_date_forecast = start_date_forecast
#        self.palomars_history = palomars_history
#
#        # Константы
#        self.SMALL_SAMPLE_SIZE = 5
#        self.EXACT_MATCH_SIZE = 3
#        self.DURATION_TOLERANCE = 0.7
#
#        # КОМБИНАЦИИ ДЛЯ ПРОГНОЗИРОВАНИЯ КРУПНЫХ И МЕЛКИХ ПРОГРАММ
#        self.combinations_list_general = []
#        self.combinations_list_general_without_dur_min  = []
#        self.combinations_list = []
#        self.combinations_list_without_dur_min = []
#        self.combinations_list_no_duration = []
#
#        # Генерируем различные комбинации для канала "МатчТВ"
#        if self.channel == 'МатчТВ':
#            # Список со всевозможными комбинациями c обязательным параметром "dur_min".
#            self.combinations_list_general = self.generate_field_combinations(
#                                        ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта', 'Метка'], 
#                                        min_fields = 6,
#                                        must_include = ['dur_min', 'dt_start', 'dt_end', 'Вид спорта', 'Метка'],
#                                        exclude = None,
#                                        debug = False
#                            )
#            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
#            self.combinations_list_general_without_dur_min = [
#                [field for field in combination if field != 'dur_min'] 
#                for combination in self.combinations_list_general
#            ]
#
#            # Генерируем комбинации C ПАРАМЕТРОМ 'dur_min' и требуем, чтобы он был обязательным
#            self.combinations_list = self.generate_field_combinations(
#                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'dur_min', 'Вид спорта', 'Метка'],  # Без 'dur_min'
#                                min_fields = 2,
#                                must_include = ['dur_min', 'Вид спорта'],
#                                exclude = None,
#                                debug = False
#                            )
#            
#            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
#            self.combinations_list_without_dur_min = [
#                [field for field in combination if field != 'dur_min'] 
#                for combination in self.combinations_list
#            ]
#
#            # Генерируем комбинации БЕЗ параметра 'dur_min', но с обязательными параметрами 'dt_start', 'dt_end'.
#            self.combinations_list_no_duration = self.generate_field_combinations(
#                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта'],  # Без 'dur_min'
#                                min_fields = 3,
#                                must_include = ['dt_start', 'dt_end', 'Вид спорта'],
#                                exclude = None,
#                                debug = False
#                            )
#            
#            
#        # Генерируем различные комбинации для всех остальных каналов
#        else:
#            # Список со всевозможными комбинациями c обязательным параметром "dur_min".
#            self.combinations_list_general = self.generate_field_combinations(
#                                        ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end'], 
#                                        min_fields = 4,
#                                        must_include = ['dur_min', 'dt_start', 'dt_end'],
#                                        exclude = None,
#                                        debug = False
#                            )
#
#            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
#            self.combinations_list_general_without_dur_min = [
#                [field for field in combination if field != 'dur_min'] 
#                for combination in self.combinations_list_general
#            ]
#            
#            # Генерируем комбинации C ПАРАМЕТРОМ 'dur_min' и требуем, чтобы он был обязательным
#            self.combinations_list = self.generate_field_combinations(
#                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'dur_min'],  # Без 'dur_min'
#                                min_fields = 2,
#                                must_include = ['dur_min'],
#                                exclude = None,
#                                debug = False
#                            )
#            
#            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
#            self.combinations_list_without_dur_min = [
#                [field for field in combination if field != 'dur_min'] 
#                for combination in self.combinations_list
#            ]
#            
#            # Генерируем комбинации БЕЗ параметра 'dur_min', но с обязательными параметрами 'dt_start', 'dt_end'.
#            self.combinations_list_no_duration = self.generate_field_combinations(
#                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end'],  # Без 'dur_min'
#                                min_fields = 3,
#                                must_include = ['dt_start', 'dt_end'],
#                                exclude = None,
#                                debug = False
#                            )
#    
#
#    @staticmethod
#    def generate_forecast_period(
#            guide: dict, 
#            months: dict, 
#            month_num: int, 
#            channel: str, 
#            year: int, 
#            n_days_in_fact: int,
#            debug = False
#        ) -> dict:
#        """
#            Метод для генерации стартовой и конечной дат прогноза.
#            Параметры:
#            ----------
#                guide: dict
#                    Словарь с информацией о БЦА для каждого канала.
#                    Ключ: название канала, Значение: БЦА
#                months: dict
#                    Справочник по месяцам
#                month_num: pd.DataFrame
#                    Родительский DataFrame для создания маски
#            Returns:
#            ----------
#                share: float
#                    Прогнозная доля
#                source_mask:
#                    Маска, которая использовалась для прогнозирования
#        """
#        if debug:
#            print(f"Количество дней в факте {n_days_in_fact} дней.")
#
#        # Определяем прогнозируемый месяц
#        month = months[month_num]
#        # Определяем ЦА, исходя из словаря self.GUIDE с каналами
#        BCA = guide[channel]
#
#        # Получаем последний день месяца
#        last_day = calendar.monthrange(year, month_num)[1]
#
#        start_of_month = f'{year}-{month_num:02d}-01'
#        finish_of_month = f'{year}-{month_num:02d}-{last_day}'
#
#        start_date_forecast = None
#        last_fact_date = None
#        # Если есть дни в факте по прогнозируемому месяцу
#        if n_days_in_fact != 0:
#            print(f"Количество дней в факте:      {n_days_in_fact}")
#            last_fact_date = f'{year}-{month_num:02d}-{n_days_in_fact}'
#            start_date_forecast = f'{year}-{month_num:02d}-{n_days_in_fact + 1}'
#
#        # Если нет накопленного факта по прогнозируемому месяцу
#        else:
#            start_date_forecast = start_of_month
#
#        print(f"Начало месяца:                     {start_of_month}")
#        print(f"Дата начала построения прогноза:   {start_date_forecast}")
#        print(f"Последняя прогнозируемая дата:     {finish_of_month}")
#        print(f"Последняя фактическая дата:        {last_fact_date}")
#        
#        params = {
#            'BCA': BCA,
#            'month': month,
#            'start_month': start_of_month,
#            'start_date_forecast': start_date_forecast,
#            'stop_month': finish_of_month,
#            'last_fact_date': last_fact_date
#        }
#        return params
#
#    
#    @staticmethod
#    def add_duration(df):
#        """
#            Метод для добавления продолжительности программ. В результате в таблице появляется новый столбец "Продолжительность"
#        """
#        # Вспомогательная функция для округления
#        def round_to_nearest_tens(n):
#            """
#                Если число < 10, то число не округляется. В противном случае округляется по правилам математики.
#                Например, 
#                    118 -> 120, 
#                    14-> 10
#            """
#            if n < 10:
#                return n
#            else:
#                return round(n / 10) * 10
#            
#        # Считаем длительности программ
#        df['Время выхода_dt'] = pd.to_datetime(df['Время выхода'])
#        df['Время окончания_dt'] = pd.to_datetime(df['Время окончания'])
#    
#        # Автоматически корректируем переход через полночь
#        df['Время окончания_dt'] = np.where(
#            df['Время окончания_dt'] < df['Время выхода_dt'],
#            df['Время окончания_dt'] + pd.Timedelta(days = 1),
#            df['Время окончания_dt']
#        )
#    
#        # Расчет продолжительности в секундах
#        df['duration_in_sec'] = (
#            df['Время окончания_dt'] - df['Время выхода_dt']
#        ).dt.total_seconds()
#        
#        # Добавляем столбец с продолжительностью в минутах (целое число)
#        df['dur_min'] = (df['duration_in_sec'] / 60).round().astype(int)
#        df['dur_min'] = df['dur_min'].apply(round_to_nearest_tens)
#        
#        # Форматирование в ЧЧ:ММ:СС
#        df['Продолжительность'] = df['duration_in_sec'].apply(
#            lambda x: f"{int(x//3600):02d}:{int((x%3600)//60):02d}:{int(x%60):02d}"
#        )
#        
#        # Удаляем вспомогательные столбцы
#        df_new = df.drop(['Время выхода_dt', 'Время окончания_dt', 'duration_in_sec'], axis=1)
#        
#        return df_new
#
#    
#    @staticmethod
#    def build_russian_holidays(holidays_path: str):
#        """
#            Генератор праздников РФ
#            Args:
#                holidays_path: путь к файлу json, в котором перечислены все празднику согласно производственному календарю
#            Returns: 
#                holidays['working_saturdays']: список из рабочих суббот
#                all_holidays: список праздников за исключением рабочих суббот
#        """
#        with open(holidays_path, 'r', encoding = 'utf-8') as file:
#            holidays = json.load(file)
#
#        all_holidays = []
#        for year in list(holidays.keys())[1:]:
#            all_holidays.extend(holidays[year])
#        return holidays['working_saturdays'], all_holidays
#    
#
#    @staticmethod
#    def get_day_type(date, holidays: list, working_saturdays: list):
#        date_str = datetime.strftime(date, '%Y-%m-%d')
#        
#        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
#        weekday = date_obj.weekday()
#        
#        if date_str in working_saturdays:
#            return 'будний'
#            
#        elif date_str in holidays:
#            return 'выходной'
#            
#        elif weekday < 5:
#            return 'будний'
#            
#        else:
#            return 'выходной'
#    
#
#    @staticmethod
#    def get_holidays(date, holidays: list):
#        """
#            Помечает меткой 1, если день праздничный. Генерируется, исходя из производственного календаря
#        """
#        date_str = datetime.strftime(date, '%Y-%m-%d')
#        
#        if date_str in holidays:
#            return 1
#        
#        else:
#            return 0
#        
#
#    @staticmethod
#    def add_day_part(time: int):
#        """
#            Добавляет тип части суток, исходя из часа выхода/окончания программы.
#                УТРО: 06:00 - 10:00
#                ДЕНЬ: 11:00 - 19:00
#                ВЕЧЕР: 20:00 - 00:00
#                НОЧЬ: 01:00 - 05:00
#        """
#        if time in [6, 7, 8, 9, 10]:
#            return 'утро'
#
#        elif time in [11, 12, 13, 14, 15, 16, 17, 18, 19]:
#            return 'день'
#
#        elif time in [20, 21, 22, 23, 0]:
#            return 'вечер'
#
#        else:
#            return 'ночь'
#    
#
#    @staticmethod
#    def get_clean_mean(series):
#        """
#            Обработать выбросы и вернуть среднее значение
#        """
#        if len(series) == 0:
#            return 0.0
#        clean_shares = TimeSeriesTransformer(series).replace_outliers_with_median()
#        return np.mean(clean_shares)
#
#
#    def calculate_share(
#            self, 
#            data: pd.DataFrame, 
#            source_mask: pd.Series = None, 
#            parent_df: pd.DataFrame = None
#        ):
#        """
#            Расчет средней доли с возвратом использованной маски
#            Параметры:
#            ----------
#                data: pd.DataFrame
#                    Отфильтрованные данные
#                source_mask: pd.Series
#                    Исходная маска (опционально)
#                parent_df: pd.DataFrame
#                    Родительский DataFrame для создания маски
#            Returns:
#            ----------
#                share: float
#                    Прогнозная доля
#                source_mask:
#                    Маска, которая использовалась для прогнозирования
#        """
#        if len(data) == 0:
#            return 0.0, None
#        
#        data_tail = pd.DataFrame()
#
#        if len(data) >= self.EXACT_MATCH_SIZE:
#            # Берем последние 3 записи
#            data_tail = data.tail(self.EXACT_MATCH_SIZE)
#        else:
#            data_tail = data
#        
#        if source_mask is not None:
#            # Создаем маску на основе source_mask, но оставляем только tail индексы
#            used_mask = pd.Series(False, index = source_mask.index)
#            tail_indices = data_tail.index
#            used_mask[tail_indices] = True
#        elif parent_df is not None:
#            # Создаем маску на основе parent_df
#            used_mask = pd.Series(False, index = parent_df.index)
#            used_mask[data_tail.index] = True
#        else:
#            used_mask = None
#
#        return float(np.mean(data_tail['Share'])), used_mask
#    
#    
#
#    def find_by_duration(
#        self,
#        data: pd.DataFrame, 
#        dur_min: int, 
#        use_tolerance: bool = False,
#        base_mask: pd.Series = None
#        ):
#        """
#            Поиск по длительности с возвратом маски
#
#            Параметры:
#            ----------
#        """
#        if use_tolerance:
#            mask = (data['dur_min'] >= dur_min * self.DURATION_TOLERANCE) & \
#                (data['dur_min'] <= dur_min / self.DURATION_TOLERANCE)
#        else:
#            mask = data['dur_min'] == dur_min
#        
#        # Сохраняем индексы до reset_index
#        original_indices = data[mask].index
#        
#        filtered_data = data[mask].reset_index(drop=True)
#        
#        # Комбинируем маски
#        if base_mask is not None:
#            combined_mask = base_mask.copy()
#            # Оставляем только те индексы, которые прошли фильтр длительности
#            combined_mask[~combined_mask.index.isin(original_indices)] = False
#        else:
#            combined_mask = pd.Series(False, index = data.index)
#            combined_mask[original_indices] = True
#        
#        share, _ = self.calculate_share(filtered_data, combined_mask, parent_df = data)
#        return share, combined_mask if combined_mask.any() else None
#
#
#    def generate_field_combinations(
#                                self,
#                                fields, 
#                                min_fields = 2, 
#                                must_include = None, 
#                                exclude = None, 
#                                debug = False
#                            ):
#        """
#            Универсальная функция для генерации комбинаций полей
#            
#            Параметры:
#            ----------
#            fields : list
#                Список всех доступных полей
#            min_fields : int, default=2
#                Минимальное количество полей в комбинации
#            must_include : list or str, optional
#                Поле(я), которые ДОЛЖНЫ быть в каждой комбинации
#            exclude : list or str, optional
#                Поле(я), которые НЕ ДОЛЖНЫ участвовать в комбинациях
#            debug : bool, default=False
#                Печатать отладочную информацию
#            
#            Returns:
#            --------
#            list
#                Список комбинаций полей в порядке убывания длины
#        """
#        # Нормализуем входные параметры
#        if must_include is None:
#            must_include = []
#        elif isinstance(must_include, str):
#            must_include = [must_include]
#        
#        if exclude is None:
#            exclude = []
#        elif isinstance(exclude, str):
#            exclude = [exclude]
#        
#        # Исключаем ненужные поля
#        working_fields = [f for f in fields if f not in exclude]
#        
#        # Отделяем обязательные поля от опциональных
#        mandatory = [f for f in must_include if f in working_fields]
#        optional = [f for f in working_fields if f not in mandatory]
#        
#        combinations_list = []
#        
#        # Минимальное количество опциональных полей
#        min_optional = max(0, min_fields - len(mandatory))
#        
#        # Генерируем комбинации опциональных полей
#        # Идем от максимального количества к минимальному
#        for i in range(len(optional), min_optional - 1, -1):
#            for combo in combinations(optional, i):
#                # Добавляем обязательные поля
#                full_combo = mandatory + list(combo)
#                combinations_list.append(full_combo)
#        
#        if debug:
#            print(f"\n{'='*50}")
#            print("ПАРАМЕТРЫ ГЕНЕРАЦИИ:")
#            print(f"{'=' * 50}")
#            print(f"Всего полей: {fields}")
#            print(f"Исключены: {exclude if exclude else 'нет'}")
#            print(f"Обязательные: {mandatory if mandatory else 'нет'}")
#            print(f"Опциональные: {optional}")
#            print(f"Минимальное количество полей: {min_fields}")
#            print(f"{'='*50}")
#            print(f"Сгенерировано комбинаций: {len(combinations_list)}")
#            print(f"{'='*50}")
#            
#            if combinations_list:
#                print("\nКОМБИНАЦИИ (в порядке приоритета):")
#                for i, combo in enumerate(combinations_list, 1):
#                    print(f"{i:2d}. {combo}")
#            print(f"{'=' * 50}\n")
#        
#        return combinations_list
#    
#
#    def prepare_forecast_inputs(
#            self, 
#            df: pd.DataFrame,
#            program_name: str,
#            all_holidays, 
#            work_saturdays, 
#            n_weeks_ago: int = 3,
#            debug = False
#            ):
#        """
#            Строит датасет для программы, добавляет признаки.
#            
#            Параметры:
#            ----------
#                df: pd.DataFrame
#                    Таблица, с которой будем работать
#                program_name : str
#                    Название программы
#                all_holidays : 
#                    Российские праздники
#                work_saturdays :
#                    Рабочие субботы РФ
#                n_weeks_ago : int, default = 3
#                   Количество последних недель, которые идут в расчет
#            
#            Returns:
#            --------
#            pd.DataFrame
#                Таблица из последних N недель для какой-то программы
#
#        """
#        columns_order = []
#        palomars_last_n_weeks = pd.DataFrame()
#        last_n_weeks = pd.DataFrame()
#
#        palomars_history = self.palomars_history.copy()
#        palomars_history['Дата'] = pd.to_datetime(palomars_history['Дата'])
#
#         # Последняя фактическая дата из истории.
#        last_fact_date = palomars_history['Дата'].max()
#
#        palomars_history['День недели'] = palomars_history['Дата'].dt.strftime('%A')
#        if 'program_name' in palomars_history.columns:
#            palomars_history.rename(columns = {'program_name': 'Название программы'}, inplace = True)
#
#        
#        df_copy = df.copy()
#
#        # Шаг 2. Добавление длительности программ.
#        data = RuleBasedForecaster.add_duration(df_copy)
#        palomars_history = RuleBasedForecaster.add_duration(palomars_history)
#
#        # Шаг 3. Определение типа дня: 0 - Будни, 1 - Выходные
#        data['Тип дня'] = data['Дата'].apply(lambda x: RuleBasedForecaster.get_day_type(x, all_holidays, work_saturdays))
#        palomars_history['Тип дня'] = palomars_history['Дата'].apply(lambda x: RuleBasedForecaster.get_day_type(x, all_holidays, work_saturdays))
#
#
#        columns = ['Время выхода', 'Время окончания']
#        for column in columns:
#            data[f'{column}_dt'] = pd.to_datetime(data[column], format = '%H:%M:%S')
#            palomars_history[f'{column}_dt'] = pd.to_datetime(palomars_history[column], format = '%H:%M:%S')
#
#            data['hour'] = pd.to_datetime(data[f'{column}_dt']).dt.hour
#            palomars_history['hour'] = pd.to_datetime(palomars_history[f'{column}_dt']).dt.hour
#
#            if column == 'Время выхода':
#                data['dt_start'] = data['hour'].apply(RuleBasedForecaster.add_day_part)
#                palomars_history['dt_start'] = palomars_history['hour'].apply(RuleBasedForecaster.add_day_part)
#            else:
#                data['dt_end'] = data['hour'].apply(RuleBasedForecaster.add_day_part)
#                palomars_history['dt_end'] = palomars_history['hour'].apply(RuleBasedForecaster.add_day_part)
#
#            data = data.drop([f'{column}_dt', 'hour'], axis = 1)
#            palomars_history = palomars_history.drop([f'{column}_dt', 'hour'], axis = 1)
#
#
#        if debug:
#            print('=============== ЗАПУСКАЮ ДЕБАГГЕР ===============\n')
#            print(f'Анализ программы: {program_name}')
#        
#        if self.channel == 'МатчТВ':
#            columns_order = [
#                'Дата', 'Название программы', 'Время выхода', 
#                'Время окончания', 'Продолжительность',
#                'dur_min', 'День недели', 'Тип дня',
#                'dt_start', 'dt_end', 'Вид спорта', 'Метка', 'Share'
#                        ]
#        else:
#            columns_order = [
#                'Дата', 'Название программы', 'Время выхода', 
#                'Время окончания', 'Продолжительность',
#                'dur_min', 'День недели', 'Тип дня',
#                'dt_start', 'dt_end', 'Share'
#                        ]
#
#        
#        # Шаг 4. Отбираем ТОЛЬКО те даты, которые нужно спрогнозировать
#        per_forecast = data[data['Share'] == ''].reset_index(drop = True)
#        per_forecast = per_forecast[columns_order]
#        if debug:
#            print(f'Всего требуется спрогнозировать {len(per_forecast)} различных дней-слотов.')
#        
#        dates_per_forecast = per_forecast['Дата'].unique()
#        if debug:
#            print(f'Всего требуется спрогнозировать: {len(dates_per_forecast)} уникальных дат.')
#
#        
#        # Шаг 5. Отбираем ТОЛЬКО исторические значения из исходного датафрейма для конкретной программы
#        history = data[data['Share'] != ''].reset_index(drop = True)
#        history = history[columns_order]
#
#        if self.channel == 'МатчТВ':
#            palomars_history = palomars_history[[
#                        'Дата', 'Название программы', 'Время выхода', 
#                        'Время окончания', 'Продолжительность', 'Жанр',
#                        'dur_min', 'День недели', 'Тип дня',
#                        'dt_start', 'dt_end', 'Вид спорта', 'Метка', 'Share'
#                            ]]
#        else:
#            palomars_history = palomars_history[[
#                        'Дата', 'Название программы', 'Время выхода', 
#                        'Время окончания', 'Продолжительность', 'Жанр',
#                        'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Share'
#                            ]]
#
#        
#        current_year = pd.DataFrame()
#        # Шаг 6. Отбор ТОЛЬКО текущего года
#        # Если прогнозируемый месяц январь, то отбираем все данные, начиная с прошлого года.
#        if self.month_num == 1:
#            # Отбираем данные для конкретной программы
#            current_year_mask = (history['Дата'] >= f'{self.current_year - 1}-01-01') & \
#                                (history['Дата'] < self.start_date_forecast)
#            current_year = history[current_year_mask].reset_index(drop = True)
#
#            # Отбираем все исторические данные
#            current_year_mask_palomars = (palomars_history['Дата'] >= f'{self.current_year - 1}-01-01') & \
#                                         (palomars_history['Дата'] < self.start_date_forecast)
#            palomars_history = palomars_history[current_year_mask_palomars].reset_index(drop = True)
#
#        else:
#            current_year_mask = (history['Дата'] >= f'{self.current_year}-01-01') & \
#                                (history['Дата'] < self.start_date_forecast)
#            current_year = history[current_year_mask].reset_index(drop = True)
#            
#            current_year_mask_palomars = (palomars_history['Дата'] >= f'{self.current_year}-01-01') & \
#                                         (palomars_history['Дата'] < self.start_date_forecast)
#            palomars_history = palomars_history[current_year_mask_palomars].reset_index(drop = True)
#        
#        if len(current_year) != 0:
#            
#            # Шаг 7. Последняя фактическая дата из истории.
#            if debug:
#                print(f"Последняя фактическая дата: {last_fact_date.strftime('%Y-%m-%d')}.")
#
#            # Шаг 7. Отбор ПОСЛЕДНИХ N НЕДЕЛЬ, исходя из максимальной фактической даты 
#            date_n_weeks_ago = last_fact_date - pd.Timedelta(weeks = n_weeks_ago)
#            if debug:
#                print(f"Последние {n_weeks_ago} недели: {date_n_weeks_ago.strftime('%Y-%m-%d')} - {last_fact_date.strftime('%Y-%m-%d')}.")
#            last_n_weeks = history[history['Дата'] > date_n_weeks_ago].reset_index(drop = True).copy()
#
#            palomars_last_n_weeks =  palomars_history[palomars_history['Дата'] > date_n_weeks_ago].reset_index(drop = True).copy()
#
#        else:
#            # Отбираем после N недель из истории Palomars
#            if debug:
#                print('Программы ' + Color.BLUE + f"'{program_name}'" + Color.END + ' ещё не было в текущем году.')
#                print(f"Последняя фактическая дата в истории Palomars: {last_fact_date.strftime('%Y-%m-%d')}.")
#            date_n_weeks_ago = last_fact_date - pd.Timedelta(weeks = n_weeks_ago)
#
#            if debug:
#                print(f"Последние {n_weeks_ago} недели из Palomars: {date_n_weeks_ago.strftime('%Y-%m-%d')} - {last_fact_date.strftime('%Y-%m-%d')}.")
#
#            last_n_weeks = pd.DataFrame()
#            palomars_last_n_weeks =  palomars_history[palomars_history['Дата'] > date_n_weeks_ago].reset_index(drop = True).copy()
#
#        program_forecast_package = {
#            'target_dates': dates_per_forecast,         # даты, на которые нужен прогноз
#            'target_data': per_forecast,                # данные для прогнозирования
#            'history_last_n_weeks': last_n_weeks,       # история программы за N недель
#            'palomars_history': palomars_last_n_weeks,  # история Palomars за N недель
#            'full_palomars_history': palomars_history,  # полная история Palomars
#        }
#
#        return program_forecast_package
#
#
#    def _try_find(self, data, condition, fields, dur_gap = False, debug = False):
#        """
#            Проверяет условие и возвращает результат, если есть данные
#            
#            Returns:
#                tuple: (share_mean, used_mask, found)
#        """
#        data_copy = data.copy()
#        filtered_data = data_copy[condition].reset_index(drop = True)
#
#        if len(filtered_data) > 1:
#            share_mean, used_mask = self.calculate_share(filtered_data, condition)
#            if debug:
#                    print(f'Количество записей в выборке {len(filtered_data)}. Данные были найдены по полям {", ".join(fields)}.')
#            return share_mean, used_mask, True
#        
#        elif len(filtered_data) == 1:
#            if dur_gap == False:
#                if debug:
#                    print(f'Количество записей в выборке 1. Данные были найдены по полям {", ".join(fields)}.')
#                return filtered_data['Share'].iloc[0], condition, True
#            else:
#                return 0.0, None, False
#            
#        else:
#            return 0.0, None, False
#    
#    
#
#    def _search_by_combinations(
#            self, 
#            data: pd.DataFrame, 
#            whole_history: pd.DataFrame, 
#            search_values: dict, 
#            debug: bool = False
#        ):
#        """
#            Внутренний метод для поиска по комбинациям полей.
#    
#            Параметры:
#            ----------
#            data : pd.DataFrame
#                Датафрейм, с помощью которого будем строить прогноз
#            whole_history: pd.DataFrame
#                Датафрейм с историей
#            search_values : dict
#                Словарь с искомыми значениями
#            debug : bool
#                Режим отладки
#            
#            Returns:
#            -------
#            tuple: (share_mean, used_mask, found)
#                share_mean: рассчитанная доля
#                used_mask: использованная маска
#                found: bool - найден ли результат
#        """
#        if len(data) == 0:
#            raise ValueError(f"Таблица с историей пустая! Не могу построить прогноз.")
#        
#        sport_type = None
#
#        # Задаем параметры
#        share_mean = 0.0
#        used_mask = None
#        found = False  # Флаг, что результат найден
#        
#        # Тип части дня начала программы
#        day_part_start = search_values['dt_start']
#        # Тип части дня окончания программы
#        day_part_end = search_values['dt_end']
#
#        # Вычленяем длительность программы.
#        dur_min = search_values['dur_min']
#
#        if self.channel == 'МатчТВ':
#            sport_type = search_values['Вид спорта']
#
#        # Определяем границы люфта
#        dur_min_lower = dur_min * 0.7  # -30%
#        dur_min_upper = dur_min * 1.3  # +30%
#
#        # Для канала '2X2' оставляем возможность присутствия нуль
#        if self.channel != '2X2':
#            if 'Share' not in data.columns:
#                raise ValueError(f"Колонка 'Share' отсутствует в данных. Доступные колонки: {data.columns.tolist()}")
#            # Отфильтровываем ненулевые значения долей (чтобы случайно нули не попали в усреднение и тем самым занизили прогноз)
#            data = data[data['Share'] != 0].reset_index(drop = True)
#
#        # Все стратегии поиска: (комбинации, использовать_люфт, обязательна_длительность, комментарий, особенный ключ)
#        strategies = [
#            # 1ый проход: обязательные параметры "dur_min", "dt_start", "dt_end". Мин. кол-во элементов в комбинации: 4.
#            (self.combinations_list_general, False, True, 
#             f'🔍 Осуществляем поиск по списку с обязательными параметрами "dur_min", "dt_start", "dt_end". ' + \
#             f'Минимальное количество параметров в комбинации 4.',
#             None
#            ),
#            # 2ой проход: обязательный параметр "dur_min". Мин. кол-во элементов в комбинации: 2.
#            (self.combinations_list, False, True,
#             f'🔍 Осуществляем поиск по списку с обязательными параметрами "dur_min".  ' + \
#             f'Минимальное количество параметров в комбинации 2.', 
#             None
#            ),
#            # 3ий проход: Аналогичен пункту 1, но в длительность добавляется люфт ±30%
#            (self.combinations_list_general_without_dur_min, True, True,
#             f'🔍 Осуществляем поиск по списку с обязательными параметрами "dur_min", "dt_start", "dt_end". ' + \
#             f'Минимальное количество параметров в комбинации 4.',
#             None
#             ),
#            # 4ый проход: Аналогичен пункту 2, но в длительность добавляется люфт ±30%
#            (self.combinations_list_without_dur_min, True, True,
#             f'🔍 Осуществляем поиск по списку с обязательными параметрами "dur_min" с люфтом ' + \
#             f'Минимальное количество параметров в комбинации 2.', 
#             None
#             ),
#            # 5ый проход: обязательные параметры "dt_start", "dt_end". Добавляется люфт ±30% в параметр "dur_min". Мин. кол-во элементов в комбинации: 3.
#            (self.combinations_list_no_duration, True, True,
#             f'🔍 Осуществляем поиск по списку с обязательными параметрами "dt_start", "dt_end". ' + \
#             f'Минимальное количество параметров в комбинации 3. В параметр "dur_min" добавляем люфт ±30%', 
#             None
#             ),
#            # 6ой проход: поиск ТОЛЬКО по ТИПУ ЧАСТИ ДНЯ начала и окончания программы, а также по длительности
#            (None, False, False, 'Пробую поиск ТОЛЬКО по ТИПУ ЧАСТИ ДНЯ начала и окончания программы, а также по длительности', 'special_day_part'),
#            # 7ой проход: поиск ТОЛЬКО по длительности
#            (None, False, False, 'Пробую поиск ТОЛЬКО по длительности', 'special_duration'),
#            # 8ой проход: поиск ТОЛЬКО по длительности c люфтом ±30%
#            (None, False, False, 'Пробую поиск ТОЛЬКО по длительности с люфтом ±30%', 'special_duration_with_gap'),
#        ]
#
#        # Перебираем по всем различным комбинациям, составленным выше
#        for idx, (combo_list, use_gap, require_dur, comment, special_key) in enumerate(strategies):
#            if found:
#                break
#
#            if debug:
#                # Печатаем заголовок при итерации по первому элементу массива
#                if idx == 0:
#                    print(Color.VIOLET + f'Попытка построить прогноз, опираясь на целевую длительность.' + Color.END)
#
#                # Печатаем заголовок при итерации по третьему элементу массива
#                elif idx == 2: 
#                    print(Color.VIOLET + f'Попытка построить прогноз, добавляя люфт ±30% в параметр длительность.' + Color.END)
#            
#            # Если специальный ключ не указан
#            if special_key is None:
#                for fields in combo_list:
#                    condition = pd.Series(True, index = data.index)
#                    for field in fields:
#                        condition &= (data[field] == search_values[field])
#                    
#                    if not condition.any():
#                        if debug:
#                            print(f"  ❌ Нет совпадений по полям: {fields}")
#                        continue
#
#                    dur_gap = False
#                    
#                    if use_gap:
#                        condition &= (data['dur_min'] >= dur_min_lower) & (data['dur_min'] <= dur_min_upper)
#                        dur_gap = True
#                    
#                    share_mean, used_mask, found = self._try_find(data, condition, fields, dur_gap = dur_gap, debug = debug)
#                    if found:
#                        break # выход из внутреннего цикла
#                
#                if found:
#                    continue  # Переход к следующей стратегии
#            
#            # ШЕСТОЙ ПРОХОД
#            elif special_key == 'special_day_part':
#                if debug:
#                    print(Color.ITALIC + '🔍 Пробую поиск ТОЛЬКО по ТИПУ ЧАСТИ ДНЯ начала и окончания программы, а также по длительности ...' + Color.END)
#                day_part_mask = (data['dt_start'] == day_part_start) & (data['dt_end'] == day_part_end)
#            
#                # -------- ДОБАВЛЯЕМ ДЛИТЕЛЬНОСТЬ СЮДА --------
#                duration_mask = (data['dur_min'] == dur_min)
#
#                # Комбинируем условия
#                final_condition = day_part_mask & duration_mask
#                filtered_data = data[final_condition].reset_index(drop = True)
#
#                # Шаг 1. Попытка построить прогноз на основании найденной mask
#                if len(filtered_data) > 1:
#                    share_mean, used_mask = self.calculate_share(filtered_data, final_condition)
#                    found = True
#                    if debug:
#                        print(f'✅ Найдено {len(filtered_data)} записей по типу части дня начала и окончания программы, а также по длительности.')
#                    
#                    continue
#                
#                # Добавляем люфт в длительность
#                else:
#                    if debug:
#                        print('🔍 Пробую поиск ТОЛЬКО по ТИПУ ЧАСТИ ДНЯ начала и окончания программы, а также по длительности с люфтом ...')
#                    duration_backlash_mask = (data['dur_min'] >= dur_min_lower) & \
#                                            (data['dur_min'] <= dur_min_upper)
#                    
#                    # Комбинируем условия
#                    final_condition = day_part_mask & duration_backlash_mask
#                    filtered_data = data[final_condition].reset_index(drop = True)
#
#                    # Шаг 1. Попытка построить прогноз на основании найденной mask
#                    if len(filtered_data) > 1:
#                        share_mean, used_mask = self.calculate_share(filtered_data, final_condition)
#                        found = True
#                        if debug:
#                            print(
#                                f'✅ Найдено {len(filtered_data)} записей по типу части дня начала и окончания программы, а также по длительности с люфтом ±30%.'
#                            )
#                        continue
#                
#                    else:
#                        if debug:
#                            print('🔍 Пробую поиск ТОЛЬКО по ТИПУ ЧАСТИ ДНЯ начала и окончания программы ...')
#
#                        # Комбинируем условия
#                        final_condition = day_part_mask
#                        filtered_data = data[final_condition].reset_index(drop = True)
#
#                        # Шаг 1. Попытка построить прогноз на основании найденной mask
#                        if len(filtered_data) > 1:
#                            target_dur = dur_min
#
#                            unique_durations = filtered_data['dur_min'].unique()
#                            deltas = {}
#                            for duration in unique_durations:
#                                delta = np.abs(duration - target_dur)
#                                deltas[duration] = delta
#                            
#                            min_key = min(deltas, key = deltas.get)
#
#                            # Проверяем, что min_key находится в диапазоне [0.5*dur_min, 1.5*dur_min]
#                            if (min_key >= 0.5 * dur_min) and (min_key <= 1.5 * dur_min):
#
#                                filtered_data_ = filtered_data[filtered_data['dur_min'] == min_key].reset_index(drop = True)
#
#                                if len(filtered_data_) > 1:
#                                    used_mask = final_condition & (filtered_data['dur_min'] == min_key)
#                                    share_mean = np.median(list(filtered_data_['Share']))
#                                    found = True
#                                    if debug:
#                                        print(f'✅ Найдено {len(filtered_data_)} записей только по длительности с минимальным расхождением с таргетом.')
#                                    continue
#
#            # СЕДЬМОЙ ПРОХОД
#            elif special_key == 'special_duration':
#
#                # Для МатчТВ сначала пробуем поиск по спорту
#                if self.channel == 'МатчТВ' and search_values['Вид спорта'] is not None:
#                    if len(data) > 1:
#                        share_mean = self._search_by_sport(data, search_values['Вид спорта'], debug = debug)
#                    
#                    if share_mean == 0.0:
#                        share_mean = self._search_by_sport(whole_history, search_values['Вид спорта'], debug = debug)
#                    
#                    # Если нашли по спорту - выходим
#                    if share_mean != 0.0:
#                        found = True
#                        used_mask = None
#                        if debug:
#                            print(f'✅ Найдено по спорту: {share_mean:.4f}')
#                        continue
#
#                # Если не нашли по спорту ИЛИ канал не МатчТВ - пробуем по длительности
#                if debug:
#                    print(Color.ITALIC + '🔍 Пробую поиск ТОЛЬКО по длительности ...' + Color.END)
#
#                duration_mask = (data['dur_min'] == dur_min)
#                filtered_data = data[duration_mask].reset_index(drop = True)
#
#                # Шаг 2. Попытка построить прогноз на основании найденной mask
#                if len(filtered_data) > 1:      
#                    share_mean = np.median(list(filtered_data['Share']))
#                    used_mask = duration_mask
#                    found = True
#                    if debug:
#                        print(f'✅ Найдено {len(filtered_data)} записей только по длительности.')
#                    continue
#
#            # ВОСЬМОЙ ПРОХОД
#            elif special_key == 'special_duration_with_gap':
#                if debug:
#                    print(Color.ITALIC  + '🔍 Пробую поиск ТОЛЬКО по длительности с люфтом ±30% ...' + Color.END)
#                
#                # Базовый люфт по длительности
#                duration_backlash_mask = (data['dur_min'] >= dur_min_lower) & (data['dur_min'] <= dur_min_upper)
#                
#                # Для МатчТВ пробуем сначала с видом спорта
#                if self.channel == 'МатчТВ' and sport_type is not None:
#                    mask_with_sport = duration_backlash_mask & (data['Вид спорта'] == sport_type)
#                    filtered_data = data[mask_with_sport].reset_index(drop = True)
#                    
#                    if len(filtered_data) > 1:
#                        share_mean = np.median(list(filtered_data['Share']))
#                        used_mask = mask_with_sport
#                        found = True
#                        if debug:
#                            print(f'✅ Найдено {len(filtered_data)} записей по длительности с люфтом ±30% и целевым видом спорта.')
#                        continue
#                
#                # Если не нашли с видом спорта или канал не МатчТВ, ищем только по длительности
#                if not found:
#                    filtered_data = data[duration_backlash_mask].reset_index(drop = True)
#                    if len(filtered_data) > 1:
#                        share_mean = np.median(list(filtered_data['Share']))
#                        used_mask = duration_backlash_mask
#                        found = True
#                        if debug:
#                            print(f'✅ Найдено {len(filtered_data)} записей только по длительности с люфтом ±30%.')
#                        continue
#        
#        # Если ничего не нашли, возвращаем нулевые значения
#        if not found:
#            if debug:
#                print('❌ Не удалось найти подходящую выборку ни в одном из проходов.')
#            return 0.0, None
#
#        return share_mean, used_mask
#
#
#    def _search_by_sport(self, palomars_history: pd.DataFrame, sport_type: str, debug: bool = False):
#        """
#            Метод для прогнозирования, опираясь на "Вид спорта", который шёл в течение текущего/прошлого года.
#            !!!ВАЖНО!!! Метод применим для канала "МатчТВ".
#            Параметры:
#            ----------
#                palomars_history: pd.DataFrame: 
#                    Датафрейм с полной историей Palomars
#                sport_type: str
#                    Вид спорта, который будем искать в истории
#                debug: bool
#                    Дебаггер
#        """
#        share_mean = 0.0
#        hist = pd.DataFrame()
#        # Если текущий месяц "Январь", то обираем весь прошлый год
#        if self.month_num == 1:
#            hist = palomars_history[palomars_history['Дата'] >= f'{self.current_year - 1}-01-01']
#        else:
#            hist = palomars_history[palomars_history['Дата'] >= f'{self.current_year}-01-01']
#        
#        if len(hist) == 0:
#            raise ValueError('Таблица с историей пустая. Проверьте входные данные.')
#
#        # Отбираем нужный вид спорта
#        target_sport_data = hist[hist['Вид спорта'] == sport_type].reset_index(drop = True)
#        
#        if len(target_sport_data) > 0:
#            if debug:
#                print('🔍 В качестве прогноза беру медиану по выбранному спорту за всю историю ...')
#            share_mean = np.median(list(target_sport_data['Share']))
#
#        return share_mean
#
#        
#
#    def forecast_big(
#            self,
#            program_name,
#            date,
#            search_values: dict,
#            last_n_weeks: pd.DataFrame,
#            palomars_last_n_weeks: pd.DataFrame,
#            palomars_history: pd.DataFrame,
#            debug = False
#        ):
#        """
#            Метод для прогнозирования программ с богатой историей.
#
#            Параметры:
#            ----------
#            date: 
#                Дата, для которой будем строить прогноз
#            program_name: str: 
#                Название программы
#            search_values: dict: 
#                Значения для поиска
#            last_n_weeks: pd.DataFrame: 
#                Датафрейм с последними N неделями для конкретной программы
#            palomars_last_n_weeks: pd.DataFrame: 
#                Датафрейм с последними N неделями Palomars (внезависимости от программы)
#            palomars_history: pd.DataFrame: 
#                Датафрейм со всей историей Palomars
#            debug: bool
#                Дебаггер
#            
#            Returns:
#            ----------
#            share_mean: float
#                Прогнозное значение доли
#            mask: ps.Series
#                Маска, которая использовалась для прогнозирования
#        """
#        # Задаем параметры
#        share_mean = 0.0
#        used_mask = None
#
#        if len(last_n_weeks) != 0:
#            if debug:
#                print('\n')
#                print(
#                    Color.GREEN + \
#                    'СТРОЮ ПРОГНОЗ, ОПИРАЯСЬ НА ИСТОРИИ ВЫБРАННОЙ ПРОГРАММЫ ЗА ПОСЛЕДНИЕ N НЕДЕЛЬ. ПОЖАЛУЙСТА, ПОДОЖДИТЕ ...' + \
#                    Color.END
#                    )
#                print('\n')
#
#            # Шаг 1. Попытка построить прогноз, используя данные за последние N недель для конкретной программы
#            share_mean, used_mask = self._search_by_combinations(last_n_weeks, palomars_history, search_values, debug = debug)
#
#            # Шаг 2. Попытка построить прогноз, используя исторические данные по ВСЕМ программам за последние N недель
#            if np.isclose(share_mean, 0.0):
#                if debug:
#                    print('\n')
#                    print(
#                        Color.GREEN + \
#                        'ПЕРЕХОЖУ К ПОИСКУ В ИСТОРИЧЕСКОЙ СЕТКЕ ЗА ПОСЛЕДНИЕ N НЕДЕЛЬ БЕЗ УПОРА НА КОНКРЕТНУЮ ПРОГРАММУ. ПОЖАЛУЙСТА, ПОДОЖДИТЕ ...' + \
#                        Color.END
#                        )
#                    print('\n')
#                
#                share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug = debug)
#        
#        
#        else:
#            # Если прогнозируем канал "МатчТВ" и указан "Вид спорта"
#            if self.channel == 'МатчТВ' and search_values['Вид спорта'] is not None:
#                if debug:
#                    print('\n')
#                    print(
#                        Color.NAVY + \
#                        f"Нет истории за последние N недель для программы {program_name} в {date}. " + \
#                        f"Попробуем найти историю по спорту '{search_values['Вид спорта']}' в текущем году." + \
#                        Color.END
#                        )
#                
#                share_mean = self._search_by_sport(palomars_history, search_values['Вид спорта'], debug = debug)
#
#                # Если по спорту ничего не нашли - идем в общую логику (как для других каналов)
#                if share_mean == 0:
#                    if debug:
#                        print('\n')
#                        print(
#                            Color.NAVY + \
#                            f'Нет истории за последние N недель для программы {program_name} в {date}. ' + \
#                            f'При прогнозировании опираюсь на историческую сетку без упора на конкретную программу.' + \
#                            Color.END
#                            )
#                    share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug = debug)
#            
#            else:
#                if debug:
#                    print('\n')
#                    print(
#                        Color.NAVY + \
#                        f'Нет истории за последние N недель для программы {program_name} в {date}. ' + \
#                        f'При прогнозировании опираюсь на историческую сетку без упора на конкретную программу.' + \
#                        Color.END
#                        )
#                share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug = debug)
#
#        
#        # Шаг 3. Построение прогноза путем расчета среднего за последние N недель.
#        if np.isclose(share_mean, 0.0):
#            if debug:
#                print('🔍 В качестве прогноза беру медиану за последние N недель ...')
#            
#            if len(last_n_weeks) == 0:
#                share_mean = np.median(list(palomars_last_n_weeks['Share']))
#            else:
#                share_mean = np.median(list(last_n_weeks['Share']))
#        
#        return share_mean, used_mask
#            
#
#
#    def forecast_small(
#            self,
#            search_values: dict,
#            last_n_weeks: pd.DataFrame,
#            palomars_last_n_weeks: pd.DataFrame,
#            palomars_history: pd.DataFrame,
#            debug = False
#        ):
#        """
#            Метод для прогнозирования программ с маленькой историей.
#            Параметры:
#            ----------
#                date: str: 
#                    Прогнозируемая дата.
#                combinations_list: list: 
#                    Всевозможные комбинации признаков
#                search_values: dict: 
#                    Значения для поиска
#                last_n_weeks: pd.DataFrame: 
#                    Датафрейм с последними N неделями для конкретной программы
#                palomars_last_n_weeks: pd.DataFrame: 
#                    Датафрейм с последними N неделями Palomars (внезависимости от программы)
#                palomars_history: pd.DataFrame: 
#                    Датафрейм с полной историей
#                debug: bool
#                    Дебаггер
#            Returns:
#            ----------
#                share_mean: float
#                    Прогнозная доля
#                used_mask:
#                    Маска, которая использовалась для построения прогноза
#        """
#        share_mean = 0.0
#        used_mask = None
#        
#        # Случай 1: Нет истории по текущему году/программе
#        if len(last_n_weeks) == 0:
#            if debug:
#                print(f'Истории по текущему году нет. Использую историю Palomars.')
#                print(f'Генерируем всевозможные комбинации при условии, что параметр "Продолжительность" встречается в каждой.')
#            
#            # Пытаемся найти по комбинациям в истории Palomars
#            share_mean, used_mask = self._search_by_combinations(
#                palomars_last_n_weeks, palomars_history, search_values, 
#                debug = debug
#            )
#            
#            # Если ничего не нашли - берем медиану
#            if np.isclose(share_mean, 0.0):
#                if debug:
#                    print('🔍 В качестве прогноза беру медиану за последние N недель ...')
#                share_mean = np.median(palomars_last_n_weeks['Share'])
#                used_mask = None  # Для медианы маска не определена
#        
#        # Случай 2: История по текущему году/программе есть
#        else:
#            if debug:
#                print(f'История по текущему году есть ({len(last_n_weeks)} записей).')
#            
#            # Сначала пробуем найти в истории конкретной программы
#            share_mean, used_mask = self._search_by_combinations(
#                last_n_weeks, palomars_history, search_values, 
#                debug = debug
#            )
#            
#            # Если не нашли - пробуем в общей истории Palomars
#            if np.isclose(share_mean, 0.0):
#                if debug:
#                    print(f'Не удалось найти совпадений в истории программы. Попытка построить прогноз, основываясь на истории Palomars.')
#                
#                share_mean, used_mask = self._search_by_combinations(
#                    palomars_last_n_weeks, palomars_history, search_values, 
#                    debug = debug
#                )
#                
#                # Если и там не нашли - берем медиану
#                if np.isclose(share_mean, 0.0):
#                    if debug:
#                        print('🔍 В качестве прогноза беру медиану за последние N недель ...')
#                    share_mean = np.median(palomars_last_n_weeks['Share'])
#                    used_mask = None
#        
#        return share_mean, used_mask    
#
#
#    def forecast_new(
#            self,
#            search_values: dict,
#            palomars_last_n_weeks: pd.DataFrame,
#            palomars_history: pd.DataFrame,
#            debug: bool = False
#        ):
#        """
#            Метод для прогнозирования НОВЫХ программ.
#            Параметры:
#            ----------
#                date: str: 
#                    Прогнозируемая дата.
#                combinations_list: list: 
#                    Всевозможные комбинации признаков
#                search_values: dict: 
#                    Значения для поиска
#                last_n_weeks: pd.DataFrame: 
#                    Датафрейм с последними N неделями для конкретной программы
#                palomars_last_n_weeks: pd.DataFrame: 
#                    Датафрейм с последними N неделями Palomars (внезависимости от программы)
#                palomars_history: pd.DataFrame: 
#                    Датафрейм с полной историей Palomars
#                debug: bool
#                    Дебаггер
#            Returns:
#            ----------
#                share_mean: float
#                    Прогнозная доля
#                used_mask:
#                    Маска, которая использовалась для построения прогноза
#        """
#        combinations_list_general = []
#        combinations_list_no_duration = []
#
#        found = False
#        sport_type = None
#
#        dur_min = search_values['dur_min']
#
#        # Определяем границы люфта
#        dur_min_lower = dur_min * 0.7  # -30%
#        dur_min_upper = dur_min * 1.3  # +30%
#
#        share_mean = 0.0
#        used_mask = None
#
#        if self.channel == 'МатчТВ':
#            sport_type = search_values['Вид спорта']
#        
#
#        if self.channel == 'МатчТВ':
#            # Генерируем всевозможные комбинации. Требуем, чтобы "Продолжительность" фигурировала в каждом варианте.
#            combinations_list_general = self.generate_field_combinations(
#                                fields = ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта'], 
#                                min_fields = 2,
#                                must_include = ['dur_min', 'Вид спорта'],
#                                exclude = None,
#                                debug = debug
#                            )
#            
#            # Генерируем всевозможные комбинации без обязательного параметра
#            combinations_list_no_duration = self.generate_field_combinations(
#                            fields = ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта'], 
#                            min_fields = 1,
#                            must_include = ['Вид спорта'],
#                            exclude = None,
#                            debug = debug
#                        )
#        else:
#            # Генерируем всевозможные комбинации. Требуем, чтобы "Продолжительность" фигурировала в каждом варианте.
#            combinations_list_general = self.generate_field_combinations(
#                                fields = ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end'], 
#                                min_fields = 2,
#                                must_include = 'dur_min',
#                                exclude = None,
#                                debug = debug
#                            )
#            
#            # Генерируем всевозможные комбинации без обязательного параметра
#            combinations_list_no_duration = self.generate_field_combinations(
#                            fields = ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end'], 
#                            min_fields = 1,
#                            must_include = None,
#                            exclude = None,
#                            debug = debug
#                        )
#        
#        # ========== ПЕРВЫЙ ПРОХОД: БЕЗ ЛЮФТА ==========
#        if debug:
#            print(Color.VIOLET + 'Строю прогноз, опираясь на список комбинаций с обязательным параметром "dur_min". ' + \
#                'Минимальное количество параметров в комбинации 2.' + Color.END)
#
#        for fields in combinations_list_general:
#            # Создаем маску для комбинации полей
#            condition = pd.Series(True, index = palomars_last_n_weeks.index)
#            for field in fields:
#                if field in palomars_last_n_weeks.columns:
#                    condition &= (palomars_last_n_weeks[field] == search_values.get(field))
#            
#            if not condition.any():
#                if debug:
#                    print(f"  ❌ Нет совпадений по полям: {fields}")
#                continue  # ← Ищем дальше
#            
#            filtered_data = palomars_last_n_weeks[condition]
#
#            if len(filtered_data) <= self.SMALL_SAMPLE_SIZE:
#                if len(filtered_data) == self.EXACT_MATCH_SIZE:
#                    share_mean, used_mask = self.calculate_share(filtered_data, condition, parent_df = palomars_last_n_weeks)
#                    found = True
#                    if debug:
#                        print(f"  📊 Выборка маленькая. Использую точное среднее по {len(filtered_data)} записям: {share_mean:.4f}")
#                        print(f"  ✅ Нашёл совпадения по полям: {fields}")
#                else:
#                    share_mean = RuleBasedForecaster.get_clean_mean(filtered_data['Share'])
#                    used_mask = condition
#                    found = True
#                    if debug:
#                        print(f"  📊 Выборка мала ({len(filtered_data)} записей), среднее: {share_mean:.4f}")
#                        print(f"  ✅ Нашёл совпадения по полям: {fields}")
#                break  # Нашли - выходим
#            
#            else:  # Большая выборка
#                duration_mask = filtered_data['dur_min'] >= dur_min
#                duration_filtered = filtered_data[duration_mask].reset_index(drop = True)
#                
#                if debug:
#                    print(f"  📊 Выборка большая. Фильтрация по длительности ≥ {dur_min} мин: найдено {len(duration_filtered)} записей")
#                
#                if len(duration_filtered) > 0:
#                    combined_mask = condition.copy()
#                    valid_indices = filtered_data[duration_mask].index
#                    combined_mask[~combined_mask.index.isin(valid_indices)] = False
#                    
#                    share_mean, used_mask = self.calculate_share(duration_filtered, combined_mask, parent_df = palomars_last_n_weeks)
#                    found = True
#                    
#                    if debug and not np.isclose(share_mean, 0.0):
#                        print(f"  📈 Рассчитано среднее: {share_mean:.4f}")
#                    break  # Нашли - выходим
#                # Если не нашли, продолжаем цикл (без break)
#        
#        # ========== ВТОРОЙ ПРОХОД: С ЛЮФТОМ ==========
#        # Осуществляем поиск по комбинациям без ДЛИТЕЛЬНОСТИ, при этом добавляем люфт в ДЛИТЕЛЬНОСТЬ.
#        if not found and np.isclose(share_mean, 0.0):
#
#            if self.channel == 'МатчТВ' and search_values['Вид спорта'] is not None:
#                if debug:
#                    print('\n')
#                    print(
#                        Color.NAVY + 
#                        f'Не удалось найти совпадений по полям выше. ' + 
#                        f"Попробуем найти историю по спорту '{search_values['Вид спорта']}' в текущем году." + 
#                        Color.END
#                    )
#                
#                share_mean = self._search_by_sport(palomars_history, search_values['Вид спорта'], debug = debug)
#                if share_mean != 0.0:
#                    found = True
#            
#                # Поиск с люфтом
#                if not found:
#
#                    if debug:
#                        print(Color.VIOLET + 'Добавляю люфт ±30% в длительность и снова делаю проход по комбинациям. ' + \
#                            'Минимальное количество параметров в комбинации 2.' + Color.END)
#
#                    duration_backlash_mask = (palomars_last_n_weeks['dur_min'] >= dur_min_lower) & \
#                                            (palomars_last_n_weeks['dur_min'] <= dur_min_upper)
#                    
#                    for fields in combinations_list_no_duration:
#                        # Создаем маску для комбинации полей
#                        condition = pd.Series(True, index = palomars_last_n_weeks.index)
#                        for field in fields:
#                            if field in palomars_last_n_weeks.columns:
#                                condition &= (palomars_last_n_weeks[field] == search_values.get(field))
#                        
#                        if not condition.any():
#                            if debug:
#                                print(f"  ❌ Нет совпадений по полям: {fields}")
#                            continue
#
#                        # Комбинируем условия
#                        final_condition = condition & duration_backlash_mask
#
#                        if not final_condition.any():
#                            if debug:
#                                print(f"  ❌ Нет совпадений по полям {fields} с люфтом по длительности")
#                            continue
#
#                        filtered_data = palomars_last_n_weeks[final_condition]
#
#                        # ========== Обработка в зависимости от размера выборки ==========
#                        # Случай 2: Маленькая выборка (≤ SMALL_SAMPLE_SIZE)
#                        if len(filtered_data) > 1:
#                            if len(filtered_data) >= self.EXACT_MATCH_SIZE:
#                                # Ровно 3 записи - используем точное среднее
#                                share_mean, used_mask = self.calculate_share(
#                                    filtered_data, final_condition, parent_df = palomars_last_n_weeks
#                                )
#                                found = True
#                                if debug:
#                                    print(f"  📊 Использую точное среднее по {len(filtered_data)} записям: {share_mean:.4f}")
#                                    print(f"  ✅ Нашёл совпадения по полям: {fields}")
#                            else:
#                                # Меньше 3 записей - тоже используем среднее, но без маски
#                                share_mean = RuleBasedForecaster.get_clean_mean(filtered_data['Share'])
#                                used_mask = final_condition
#                                found = True
#                                if debug:
#                                    print(f"  📊 Выборка мала ({len(filtered_data)} записей), среднее: {share_mean:.4f}")
#                                    print(f"  ✅ Нашёл совпадения по полям: {fields}")
#                            break
#            
#            else:
#                if debug:
#                    print(Color.VIOLET + 'Добавляю люфт ±30% в длительность и снова делаю проход по комбинациям. ' + \
#                        'Минимальное количество параметров в комбинации 2.' + Color.END)
#
#                duration_backlash_mask = (palomars_last_n_weeks['dur_min'] >= dur_min_lower) & \
#                                        (palomars_last_n_weeks['dur_min'] <= dur_min_upper)
#                
#                for fields in combinations_list_no_duration:
#                    # Создаем маску для комбинации полей
#                    condition = pd.Series(True, index = palomars_last_n_weeks.index)
#                    for field in fields:
#                        if field in palomars_last_n_weeks.columns:
#                            condition &= (palomars_last_n_weeks[field] == search_values.get(field))
#                    
#                    if not condition.any():
#                        if debug:
#                            print(f"  ❌ Нет совпадений по полям: {fields}")
#                        continue
#
#                    # Комбинируем условия
#                    final_condition = condition & duration_backlash_mask
#
#                    if not final_condition.any():
#                        if debug:
#                            print(f"  ❌ Нет совпадений по полям {fields} с люфтом по длительности")
#                        continue
#
#                    filtered_data = palomars_last_n_weeks[final_condition]
#
#                    # ========== Обработка в зависимости от размера выборки ==========
#                    # Случай 2: Маленькая выборка (≤ SMALL_SAMPLE_SIZE)
#                    if len(filtered_data) > 1:
#                        if len(filtered_data) >= self.EXACT_MATCH_SIZE:
#                            # Ровно 3 записи - используем точное среднее
#                            share_mean, used_mask = self.calculate_share(
#                                filtered_data, final_condition, parent_df = palomars_last_n_weeks
#                            )
#                            found = True
#                            if debug:
#                                print(f"  📊 Использую точное среднее по {len(filtered_data)} записям: {share_mean:.4f}")
#                                print(f"  ✅ Нашёл совпадения по полям: {fields}")
#                        else:
#                            # Меньше 3 записей - тоже используем среднее, но без маски
#                            share_mean = RuleBasedForecaster.get_clean_mean(filtered_data['Share'])
#                            used_mask = final_condition
#                            found = True
#                            if debug:
#                                print(f"  📊 Выборка мала ({len(filtered_data)} записей), среднее: {share_mean:.4f}")
#                                print(f"  ✅ Нашёл совпадения по полям: {fields}")
#                        break
#
#        
#        # Если ничего не нашли
#        if not found and np.isclose(share_mean, 0.0):
#            if debug:
#                print("Пробую поиск по длительности")
#            share_mean, used_mask = self.find_by_duration(palomars_last_n_weeks, dur_min, use_tolerance = False)
#            
#        if np.isclose(share_mean, 0.0):
#            share_mean, used_mask = self.find_by_duration(palomars_last_n_weeks, dur_min, use_tolerance = True)
#            if debug:
#                print(f"  🔄 Ищу с люфтом ±30%")
#                print(f"  📈 Рассчитано среднее: {share_mean:.4f}")
#        
#        # Если ничего не нашли
#        if np.isclose(share_mean, 0.0):
#            if debug:
#                print('🔍 В качестве прогноза беру медиану за последние N недель ...')
#            share_mean = np.median(list(palomars_last_n_weeks['Share']))
#        
#        return share_mean, used_mask
#    
#    
#
#    def build_forecast_per_group(
#            self,
#            programs_dict: dict,
#            all_holidays,
#            work_saturdays,
#            volume_flag: str,
#            n_weeks_ago: int,
#            debug = False
#        ):
#        """
#            Метод для построения прогноза .
#            Параметры:
#            ----------
#                programs_dict: dict 
#                    Словарь с историей программ. 
#                    Ключ - название программы, значение - датафрейм с историей, а также датами, которые нужно спрогнозировать.
#                all_holidays
#                    Праздники РФ
#                work_saturdays
#                    Рабочие субботы в РФ
#                combinations_list: list
#                    Список из всевозможных комбинаций
#                volume_flag: str
#                    Флаг, который будет указывать, какой метод для прогнозирования необходимо применять.
#                n_weeks_ago: int
#                    Количество последних недель, которое берётся для анализа
#        """
#        if volume_flag not in ['small', 'big', 'new']:
#            raise ValueError(f"Флаг '{volume_flag}' не существует. Выберите из списка: {['small', 'big', 'new']}")
#        
#        if volume_flag == 'big':
#            print(Color.BOLD + f'==== Прогнозирую крупные программы ====' + Color.END)
#        
#        elif volume_flag == 'small':
#            print(Color.BOLD + f'==== Прогнозирую мелкие программы ====' + Color.END)
#        
#        elif volume_flag == 'new':
#            print(Color.BOLD + f'==== Прогнозирую новые программы ====' + Color.END)
#
#        results_per_program = {}
#        all_masks = {}
#
#        last_n_weeks = pd.DataFrame()
#        palomars_last_n_weeks = pd.DataFrame()
#
#        for program_name in programs_dict.keys():
#        
#            df = programs_dict[program_name].reset_index(drop = True)
#
#            # Подготовка данных для прогнозирования
#            dict_analysis = self.prepare_forecast_inputs(
#                df, program_name, all_holidays, work_saturdays, 
#                n_weeks_ago = n_weeks_ago, 
#                debug = debug
#                )
#
#            last_n_weeks = dict_analysis['history_last_n_weeks']
#            palomars_last_n_weeks = dict_analysis['palomars_history']
#            full_history = dict_analysis['full_palomars_history']
#
#            if debug:
#                print(f'==== ПОСТРОЕНИЕ ПРОГНОЗА ДЛЯ ПРОГРАММЫ {program_name} ====')
#            
#            forecast_results = []
#            date_masks = {}
#            for date in dict_analysis['target_dates']:
#                if debug:
#                    print(f'------------- Прогнозирую дату {date} -------------')
#
#                # Отбор даты, которую собираемся спрогнозировать
#                per_forecast = dict_analysis['target_data']
#                future_df = per_forecast[per_forecast['Дата'] == date].reset_index(drop = True)
#
#                future_df['program_type'] = None
#                
#                share_forecast = 0.0
#                
#                # Итерируемся по выделенным данным
#                for i in range(len(future_df)):
#                    sport_type = None
#                    specific_flag = None
#
#                    time_start = future_df.iloc[i]['Время выхода']
#                    dur_min = future_df.iloc[i]['dur_min']
#                    day_of_week = future_df.iloc[i]['День недели']
#                    day_type = future_df.iloc[i]['Тип дня']
#                    dt_start = future_df.iloc[i]['dt_start']
#                    dt_end = future_df.iloc[i]['dt_end']
#
#                    if self.channel == 'МатчТВ':
#                        sport_type = future_df.iloc[i]['Вид спорта']
#                        specific_flag = future_df.iloc[i]['Метка']
#            
#                    if debug:
#                        print(f'Для {date} буду искать следующие кейсы в истории:')
#                        print('\n')
#                        print(f' - Время выхода:                            {time_start}')
#                        print(f' - Продолжительность:                       {dur_min} мин')
#                        print(f' - День недели:                             {day_of_week}')
#                        print(f' - Тип дня:                                 {day_type}')
#                        print(f' - Тип части суток начала программы:        {dt_start}')
#                        print(f' - Тип части суток окончания программы:     {dt_end}')
#                        # Добавляем дополнительный вывод параметров, если канал "МатчТВ"
#                        if self.channel == 'МатчТВ':
#                            print(f' - Вид спорта:                           {sport_type}')
#                            print(f' - Метка:                                   {specific_flag}')
#
#                        print('\n')
#                
#                    # Шаг 2. Задаем значения для поиска
#                    # Значения для поиска
#                    search_values = {
#                        'Время выхода': time_start,
#                        'dur_min': dur_min,
#                        'День недели': day_of_week,
#                        'Тип дня': day_type,
#                        'dt_start': dt_start,
#                        'dt_end': dt_end
#                    }
#                    # Добавляем дополнительные параметры, если канал "МатчТВ"
#                    if self.channel == 'МатчТВ':
#                        search_values['Вид спорта'] = sport_type
#                        search_values['Метка'] = specific_flag
#
#                    # Построение прогноза для крупной программы
#                    if volume_flag == 'big':
#                        share_forecast, mask = self.forecast_big(
#                            program_name, date, search_values, 
#                            last_n_weeks, palomars_last_n_weeks,
#                            full_history, debug = debug
#                        )
#
#                    # Построение прогноза для мелкой программы
#                    elif volume_flag == 'small':
#                        share_forecast, mask = self.forecast_small(
#                            search_values, last_n_weeks, palomars_last_n_weeks,
#                            full_history, debug = debug
#                        )
#                    
#                    # Построение прогноза для НОВОЙ программы
#                    elif volume_flag == 'new':
#                        share_forecast, mask = self.forecast_new(
#                            search_values, palomars_last_n_weeks,
#                            full_history, debug = debug)
#
#                    date_masks[date.strftime('%Y-%m-%d')] = mask
#            
#                    # Запись прогнозного значения в ячейку
#                    future_df.at[i, 'Share'] = share_forecast
#                    future_df.at[i, 'program_type'] = volume_flag
#            
#                forecast_results.append(future_df)
#                
#                if debug:
#                    print('-' * 50)
#                    print('\n')
#                    
#                all_masks[program_name] = date_masks
#                
#            results_per_program[program_name] = pd.concat(forecast_results).reset_index(drop = True)
#        return results_per_program
#    
#
#    def pipeline_forecaster(
#            self, 
#            all_programs_to_forecast: dict, 
#            all_holidays, 
#            work_saturdays,
#            fact_part_of_month: pd.DataFrame,
#            n_weeks_ago: int,
#            debug = False
#        ):
#        """
#            Пайплайн для построения прогноза совокупно для всех трёх групп: "big", "small", "new".
#            Параметры:
#            ----------
#                all_programs_to_forecast: dict of dicts
#                    Словарь словарей с разбивкой по группам программ: "big", "small", "new".
#                    Ключ - один из трёх групп: "big", "small", "new"
#                    Значение: словарь из программ с историей. Ключ - название программы, Значение - таблица с историей
#                fact_part_of_month: pd.DataFrame
#                    Таблица с фактической частью месяца
#                n_weeks_ago: int
#                    Количество последних недель, которое берётся для анализа
#        """
#        if debug:
#            if len(fact_part_of_month) != 0:
#                print('Есть накопленный факт. Учитываю это при прогнозировании.')
#            else:
#                print('Накопленного факта нет. Прогнозирую весь месяц целиком.')
#
#        # Словарь с результатми прогнозов
#        forecast_results = {}
#        for key, programs_dict in all_programs_to_forecast.items():
#            forecast_results[key] = self.build_forecast_per_group(
#                                                        programs_dict, all_holidays, 
#                                                        work_saturdays, key, n_weeks_ago
#                                                    )
#
#        results = []
#        for key, result_dict in forecast_results.items():
#            for program, forecast in result_dict.items():
#                results.append(forecast)
#
#        # Итоговая таблица с прогнозом
#        data_full = pd.concat(results).reset_index(drop = True)
#
#        columns = ['Дата', 'Название программы', 'Время выхода', 'Время окончания', 'Share', 'program_type']
#        data_full = data_full[columns]
#
#        forecast_df = pd.DataFrame()
#
#        # Если есть накопленный факт, то мы соединяем между собой две таблицы
#        if len(fact_part_of_month) != 0:
#
#            # Добавляем колонку program_type, если её нет
#            if 'program_type' not in fact_part_of_month.columns:
#                fact_part_of_month['program_type'] = 'FACT'
#
#            fact_part_of_month = fact_part_of_month[columns]
#            forecast_df = pd.concat([fact_part_of_month, data_full]).reset_index(drop = True)
#        else:
#            forecast_df = data_full
#
#        forecast_df['Дата'] = pd.to_datetime(forecast_df['Дата'])
#        sorted_webs = forecast_df.sort_values('Дата').reset_index(drop = True)
#
#        dates_unique = sorted_webs['Дата'].unique()
#        res = []
#        for date in dates_unique:
#            date_dt = pd.to_datetime(date)
#            t = sorted_webs[sorted_webs['Дата'] == date_dt]
#            t['sort_key'] = t['Время выхода'].apply(BaseParser.get_sort_key)
#            final = t.sort_values('sort_key').reset_index(drop = True)
#            final = final.drop('sort_key', axis = 1)
#            res.append(final)
#
#        general_result = pd.concat(res).reset_index(drop = True)
#        general_result['Дата'] = general_result['Дата'].dt.strftime('%Y-%m-%d')
#        # Расставляем колонки в нужном порядке
#        general_result = general_result[columns]
#        return general_result
    



class RuleBasedForecaster:
    """
    Класс с реализацией простейшей модели для прогнозирования будущих программ.
    """
    # КЭШ НА УРОВНЕ КЛАССА - общий для всех экземпляров
    _combinations_cache = {}
    _holidays_cache = {}
    _prepare_cache = {}

    # Классовые переменные для праздников (общие для всех экземпляров)
    _all_holidays = None
    _working_saturdays = None
    _holidays_tuple = None
    _work_saturdays_tuple = None
    _holidays_loaded = False
    
    def __init__(self, 
                 channel: str,
                 current_year: int,
                 month_num: int,
                 start_date_forecast: str,
                 palomars_history: pd.DataFrame,
                 holidays_path: str = None         # Путь к файлу с праздниками РФ
                ):
        self.channel = channel
        self.current_year = current_year
        self.month_num = month_num
        self.start_date_forecast = start_date_forecast
        self.palomars_history = palomars_history

        # Константы
        self.SMALL_SAMPLE_SIZE = 5
        self.EXACT_MATCH_SIZE = 3
        self.DURATION_TOLERANCE = 0.7

        # Предзагрузка праздников (один раз для всех экземпляров)
        if holidays_path and not RuleBasedForecaster._holidays_loaded:
            self._load_holidains(holidays_path)
        
        # Если праздники не были загружены, но нужны - загружаем с путём по умолчанию
        if not RuleBasedForecaster._holidays_loaded:
            self._load_holidains('russian_holidays.json')

        # Инициализация комбинаций
        self._initialize_combinations()
    

    @staticmethod
    def generate_forecast_period(
            guide: dict, 
            months: dict, 
            month_num: int, 
            channel: str, 
            year: int, 
            n_days_in_fact: int,
            debug = False
        ) -> dict:
        """
            Метод для генерации стартовой и конечной дат прогноза.
            Параметры:
            ----------
                guide: dict
                    Словарь с информацией о БЦА для каждого канала.
                    Ключ: название канала, Значение: БЦА
                months: dict
                    Справочник по месяцам
                month_num: pd.DataFrame
                    Родительский DataFrame для создания маски
            Returns:
            ----------
                share: float
                    Прогнозная доля
                source_mask:
                    Маска, которая использовалась для прогнозирования
        """
        if debug:
            print(f"Количество дней в факте {n_days_in_fact} дней.")

        # Определяем прогнозируемый месяц
        month = months[month_num]
        # Определяем ЦА, исходя из словаря self.GUIDE с каналами
        BCA = guide[channel]

        # Получаем последний день месяца
        last_day = calendar.monthrange(year, month_num)[1]

        start_of_month = f'{year}-{month_num:02d}-01'
        finish_of_month = f'{year}-{month_num:02d}-{last_day}'

        start_date_forecast = None
        last_fact_date = None
        # Если есть дни в факте по прогнозируемому месяцу
        if n_days_in_fact != 0:
            print(f"Количество дней в факте:      {n_days_in_fact}")
            last_fact_date = f'{year}-{month_num:02d}-{n_days_in_fact}'
            start_date_forecast = f'{year}-{month_num:02d}-{n_days_in_fact + 1}'

        # Если нет накопленного факта по прогнозируемому месяцу
        else:
            start_date_forecast = start_of_month

        ############### НОВЫЙ КУСОК ###############
        # Последние три месяца для выходного файла
        three_months_ago = f'{year}-{(month_num - 3):02d}-01'

        ############### КОНЕЦ НОВОГО КУСКА ###############

        print(f"Дата начала построения прогноза:   {start_date_forecast}")
        print(f"Последняя прогнозируемая дата:     {finish_of_month}")
        print(f"Три месяца назад:                  {three_months_ago}")
        
        params = {
            'BCA': BCA,
            'month': month,
            'start_month': start_of_month,
            'start_date_forecast': start_date_forecast,
            'stop_month': finish_of_month,
            'last_fact_date': last_fact_date,
            'three_months_ago': three_months_ago            # новый параметр
        }
        return params
    

    @classmethod
    def _load_holidains(cls, holidays_path: str):
        """
        Загрузка праздников на уровне класса (один раз для всех экземпляров)
        """
        # Определяем ключ для кэша на основе пути
        if holidays_path is None:
            cache_key = "holidays_default"
        else:
            cache_key = f"holidays_{holidays_path}"
        
        # Проверяем кэш
        if cache_key in cls._holidays_cache:
            (working_saturdays, all_holidays) = cls._holidays_cache[cache_key]
            cls._working_saturdays = working_saturdays
            cls._all_holidays = all_holidays
            cls._holidays_tuple = tuple(all_holidays)
            cls._work_saturdays_tuple = tuple(working_saturdays)
            print(f"✅ Праздники загружены из кэша (ключ: {cache_key})")
            return
        
        print(f"🔄 Загружаю праздники из файла {holidays_path}...")
        
        try:
            with open(holidays_path, 'r', encoding='utf-8') as file:
                holidays = json.load(file)
            
            # Извлекаем рабочие субботы
            working_saturdays = holidays.get('working_saturdays', [])
            
            # Собираем все праздники
            all_holidays = []
            for year in list(holidays.keys())[1:]:  # Пропускаем ключ 'working_saturdays'
                all_holidays.extend(holidays[year])
            
            # Сохраняем в классовые переменные
            cls._working_saturdays = working_saturdays
            cls._all_holidays = all_holidays
            cls._holidays_tuple = tuple(all_holidays)
            cls._work_saturdays_tuple = tuple(working_saturdays)
            cls._holidays_loaded = True
            
            # Сохраняем в кэш
            cls._holidays_cache[cache_key] = (working_saturdays, all_holidays)
            
            print(f"✅ Загружено {len(all_holidays)} праздников и {len(working_saturdays)} рабочих суббот")
            
        except FileNotFoundError:
            print(f"⚠️ Файл с праздниками {holidays_path} не найден. Использую пустые списки.")
            cls._working_saturdays = []
            cls._all_holidays = []
            cls._holidays_tuple = ()
            cls._work_saturdays_tuple = ()
            cls._holidays_loaded = True
        except Exception as e:
            print(f"❌ Ошибка при загрузке праздников: {e}")
            raise

    
    @classmethod
    def get_holidays(cls):
        """
        Получить праздники (ленивая загрузка, если ещё не загружены)
        """
        if not cls._holidays_loaded:
            cls._load_holidains('russian_holidays.json')
        return cls._all_holidays, cls._working_saturdays
    
    
    @classmethod
    def reload_holidays(cls, holidays_path: str):
        """
        Принудительная перезагрузка праздников (полезно при обновлении файла)
        """
        cls._holidays_loaded = False
        cls._holidays_cache.clear()  # Очищаем кэш
        cls._load_holidains(holidays_path)

    
    @staticmethod
    @lru_cache(maxsize = 1024)
    def _get_day_type_cached(date_str: str, holidays_tuple: tuple, working_saturdays_tuple: tuple):
        """Кэшированная версия get_day_type"""
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        weekday = date_obj.weekday()
        
        if date_str in working_saturdays_tuple:
            return 'будний'
        elif date_str in holidays_tuple:
            return 'выходной'
        elif weekday < 5:
            return 'будний'
        else:
            return 'выходной'
        
    
    @staticmethod
    @lru_cache(maxsize = 1024)
    def _get_holidays_cached(date_str: str, holidays_tuple: tuple):
        """Кэшированная версия get_holidays"""
        return 1 if date_str in holidays_tuple else 0
    
    
    @staticmethod
    @lru_cache(maxsize = 128)
    def _add_day_part_cached(time: int):
        """Кэшированная версия add_day_part"""
        if time in [6, 7, 8, 9, 10]:
            return 'утро'
        elif time in [11, 12, 13, 14, 15, 16, 17, 18, 19]:
            return 'день'
        elif time in [20, 21, 22, 23, 0]:
            return 'вечер'
        else:
            return 'ночь'
        
    
    def _initialize_combinations(self):
        """
        Инициализация комбинаций с использованием кэша
        """
        # Если комбинации уже загружены, пропускаем
        if hasattr(self, '_combinations_loaded') and self._combinations_loaded:
            return
    
        # Определяем ключ для кэша
        if self.channel == 'МатчТВ':
            cache_key = "combinations_matchtv"
        else:
            cache_key = "combinations_other"  # ОДИН КЛЮЧ ДЛЯ ВСЕХ ОСТАЛЬНЫХ КАНАЛОВ
        
        # Проверяем в КЛАССОВОМ кэше
        if cache_key in RuleBasedForecaster._combinations_cache:
            cached = RuleBasedForecaster._combinations_cache[cache_key]
            (
                self.combinations_list_general,
                self.combinations_list_general_without_dur_min,
                self.combinations_list,
                self.combinations_list_without_dur_min,
                self.combinations_list_no_duration,
                self.combinations_list_general_for_new_programs,      
                self.combinations_list_for_new_programs_no_duration
            ) = cached
            print(f"✅ Загружено из кэша для канала {self.channel}")
            self._combinations_loaded = True
            return
        
        print(f"🔄 Генерирую комбинации для канала {self.channel}")
        
        # Генерируем различные комбинации для канала "МатчТВ"
        if self.channel == 'МатчТВ':
            # Список со всевозможными комбинациями c обязательным параметром "dur_min".
            self.combinations_list_general = self.generate_field_combinations(
                                        ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта', 'Метка'], 
                                        min_fields = 6,
                                        must_include = ['dur_min', 'dt_start', 'dt_end', 'Вид спорта', 'Метка'],
                                        exclude = None,
                                        debug = False
                            )
            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
            self.combinations_list_general_without_dur_min = [
                [field for field in combination if field != 'dur_min'] 
                for combination in self.combinations_list_general
            ]

            # Генерируем комбинации C ПАРАМЕТРОМ 'dur_min' и требуем, чтобы он был обязательным
            self.combinations_list = self.generate_field_combinations(
                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'dur_min', 'Вид спорта', 'Метка'],
                                min_fields = 2,
                                must_include = ['dur_min', 'Вид спорта'],
                                exclude = None,
                                debug = False
                            )
            
            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
            self.combinations_list_without_dur_min = [
                [field for field in combination if field != 'dur_min'] 
                for combination in self.combinations_list
            ]

            # Генерируем комбинации БЕЗ параметра 'dur_min', но с обязательными параметрами 'dt_start', 'dt_end'.
            self.combinations_list_no_duration = self.generate_field_combinations(
                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта'],
                                min_fields = 3,
                                must_include = ['dt_start', 'dt_end', 'Вид спорта'],
                                exclude = None,
                                debug = False
                            )
            
            # Генерируем комбинации для прогнозирования новых программ. Требуем, чтобы "Вид спорта" фигурировал в каждом варианте.
            self.combinations_list_general_for_new_programs = self.generate_field_combinations(
                                fields = ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта'], 
                                min_fields = 2,
                                must_include = ['dur_min', 'Вид спорта'],
                                exclude = None,
                                debug = False
                            )
            # Генерируем комбинации для прогнозирования новых программ. 
            # Без обязательного параметра "dur_min", но с обязательным параметром "Вид спорта".
            self.combinations_list_for_new_programs_no_duration = self.generate_field_combinations(
                            fields = ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Вид спорта'], 
                            min_fields = 1,
                            must_include = ['Вид спорта'],
                            exclude = None,
                            debug = False
                        )
            
        # Генерируем различные комбинации для всех остальных каналов
        else:
            # Список со всевозможными комбинациями c обязательным параметром "dur_min".
            self.combinations_list_general = self.generate_field_combinations(
                                        ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end'], 
                                        min_fields = 4,
                                        must_include = ['dur_min', 'dt_start', 'dt_end'],
                                        exclude = None,
                                        debug = False
                            )

            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
            self.combinations_list_general_without_dur_min = [
                [field for field in combination if field != 'dur_min'] 
                for combination in self.combinations_list_general
            ]
            
            # Генерируем комбинации C ПАРАМЕТРОМ 'dur_min' и требуем, чтобы он был обязательным
            self.combinations_list = self.generate_field_combinations(
                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'dur_min'],
                                min_fields = 2,
                                must_include = ['dur_min'],
                                exclude = None,
                                debug = False
                            )
            
            # Тот же список combinations_list_general, но без обязательного параметра "dur_min"
            self.combinations_list_without_dur_min = [
                [field for field in combination if field != 'dur_min'] 
                for combination in self.combinations_list
            ]
            
            # Генерируем комбинации БЕЗ параметра 'dur_min', но с обязательными параметрами 'dt_start', 'dt_end'.
            self.combinations_list_no_duration = self.generate_field_combinations(
                                ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end'],
                                min_fields = 3,
                                must_include = ['dt_start', 'dt_end'],
                                exclude = None,
                                debug = False
                            )
            
            self.combinations_list_general_for_new_programs = self.generate_field_combinations(
                                fields = ['Время выхода', 'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end'], 
                                min_fields = 2,
                                must_include = 'dur_min',
                                exclude = None,
                                debug = False
                            )
            self.combinations_list_for_new_programs_no_duration = self.generate_field_combinations(
                            fields = ['Время выхода', 'День недели', 'Тип дня', 'dt_start', 'dt_end'], 
                            min_fields = 1,
                            must_include = None,
                            exclude = None,
                            debug = False
                        )
        
        # Сохраняем в КЛАССОВЫЙ кэш
        RuleBasedForecaster._combinations_cache[cache_key] = (
            self.combinations_list_general,
            self.combinations_list_general_without_dur_min,
            self.combinations_list,
            self.combinations_list_without_dur_min,
            self.combinations_list_no_duration,
            self.combinations_list_general_for_new_programs,
            self.combinations_list_for_new_programs_no_duration
        )
        
        self._combinations_loaded = True

    
    @staticmethod
    def add_duration(df):
        """
            Метод для добавления продолжительности программ.
        """
        def round_to_nearest_tens(n):
            if n < 10:
                return n
            else:
                return round(n / 10) * 10
            
        df['Время выхода_dt'] = pd.to_datetime(df['Время выхода'])
        df['Время окончания_dt'] = pd.to_datetime(df['Время окончания'])
    
        df['Время окончания_dt'] = np.where(
            df['Время окончания_dt'] < df['Время выхода_dt'],
            df['Время окончания_dt'] + pd.Timedelta(days=1),
            df['Время окончания_dt']
        )
    
        df['duration_in_sec'] = (
            df['Время окончания_dt'] - df['Время выхода_dt']
        ).dt.total_seconds()
        
        df['dur_min'] = (df['duration_in_sec'] / 60).round().astype(int)
        df['dur_min'] = df['dur_min'].apply(round_to_nearest_tens)
        
        df['Продолжительность'] = df['duration_in_sec'].apply(
            lambda x: f"{int(x//3600):02d}:{int((x%3600)//60):02d}:{int(x%60):02d}"
        )
        
        df_new = df.drop(['Время выхода_dt', 'Время окончания_dt', 'duration_in_sec'], axis=1)
        
        return df_new
    

    @staticmethod
    def get_day_type(date, holidays: list, working_saturdays: list):
        """
            Оригинальный метод (сохранен для совместимости)
        """
        date_str = datetime.strftime(date, '%Y-%m-%d')
        
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        weekday = date_obj.weekday()
        
        if date_str in working_saturdays:
            return 'будний'
        elif date_str in holidays:
            return 'выходной'
        elif weekday < 5:
            return 'будний'
        else:
            return 'выходной'
        

    @staticmethod
    def get_holidays_legacy(date, holidays: list):
        """
            Оригинальный метод (сохранен для совместимости)
        """
        date_str = datetime.strftime(date, '%Y-%m-%d')
        
        if date_str in holidays:
            return 1
        else:
            return 0


    @staticmethod
    def add_day_part(time: int):
        """
            Оригинальный метод (сохранен для совместимости)
        """
        if time in [6, 7, 8, 9, 10]:
            return 'утро'
        elif time in [11, 12, 13, 14, 15, 16, 17, 18, 19]:
            return 'день'
        elif time in [20, 21, 22, 23, 0]:
            return 'вечер'
        else:
            return 'ночь'
        

    @staticmethod
    def get_clean_mean(series):
        """
            Обработать выбросы и вернуть среднее значение
        """
        if len(series) == 0:
            return 0.0
        clean_shares = TimeSeriesTransformer(series).replace_outliers_with_median()
        return np.mean(clean_shares)
    

    def calculate_share(self, data: pd.DataFrame, source_mask: pd.Series = None, parent_df: pd.DataFrame = None):
        """
            Расчет средней доли с возвратом использованной маски
        """
        if len(data) == 0:
            return 0.0, None
        
        # Используем .values для ускорения
        if len(data) >= self.EXACT_MATCH_SIZE:
            data_tail = data.iloc[-self.EXACT_MATCH_SIZE:]  # tail без копирования
        else:
            data_tail = data
        
        share = data_tail['Share'].values.mean()  # .values.mean() быстрее
        
        if source_mask is not None:
            used_mask = pd.Series(False, index=source_mask.index)
            used_mask[data_tail.index] = True
        elif parent_df is not None:
            used_mask = pd.Series(False, index=parent_df.index)
            used_mask[data_tail.index] = True
        else:
            used_mask = None

        return share, used_mask
    
    
    def find_by_duration(self, data: pd.DataFrame, dur_min: int, use_tolerance: bool = False, base_mask: pd.Series = None):
        """
            Поиск по длительности с возвратом маски
        """
        # Получаем numpy массив длительностей один раз
        dur_min_values = data['dur_min'].values
        
        if use_tolerance:
            # Используем numpy для векторного сравнения
            lower_bound = dur_min * self.DURATION_TOLERANCE
            upper_bound = dur_min / self.DURATION_TOLERANCE
            mask = (dur_min_values >= lower_bound) & (dur_min_values <= upper_bound)
        else:
            mask = dur_min_values == dur_min
        
        # Получаем индексы
        original_indices = data.index[mask]
        
        # Фильтруем данные
        filtered_data = data.iloc[original_indices].reset_index(drop=True)
        
        # Комбинируем маски
        if base_mask is not None:
            combined_mask = base_mask.copy()
            # Сбрасываем все индексы, которые не прошли фильтр
            indices_set = set(original_indices)
            combined_mask[~combined_mask.index.isin(indices_set)] = False
        else:
            combined_mask = pd.Series(mask, index=data.index)
        
        share, _ = self.calculate_share(filtered_data, combined_mask, parent_df=data)
        return share, combined_mask if combined_mask.any() else None
    
    
    def generate_field_combinations(self, fields, min_fields = 2, must_include = None, exclude = None, debug = False):
        """
            Универсальная функция для генерации комбинаций полей
        """
        if must_include is None:
            must_include = []
        elif isinstance(must_include, str):
            must_include = [must_include]
        
        if exclude is None:
            exclude = []
        elif isinstance(exclude, str):
            exclude = [exclude]
        
        working_fields = [f for f in fields if f not in exclude]
        mandatory = [f for f in must_include if f in working_fields]
        optional = [f for f in working_fields if f not in mandatory]
        
        combinations_list = []
        min_optional = max(0, min_fields - len(mandatory))
        
        for i in range(len(optional), min_optional - 1, -1):
            for combo in combinations(optional, i):
                full_combo = mandatory + list(combo)
                combinations_list.append(full_combo)
        
        if debug:
            print(f"\n{'='*50}")
            print("ПАРАМЕТРЫ ГЕНЕРАЦИИ:")
            print(f"{'=' * 50}")
            print(f"Всего полей: {fields}")
            print(f"Исключены: {exclude if exclude else 'нет'}")
            print(f"Обязательные: {mandatory if mandatory else 'нет'}")
            print(f"Опциональные: {optional}")
            print(f"Минимальное количество полей: {min_fields}")
            print(f"{'='*50}")
            print(f"Сгенерировано комбинаций: {len(combinations_list)}")
            print(f"{'='*50}")
            
            if combinations_list:
                print("\nКОМБИНАЦИИ (в порядке приоритета):")
                for i, combo in enumerate(combinations_list, 1):
                    print(f"{i:2d}. {combo}")
            print(f"{'=' * 50}\n")
        
        return combinations_list
    

    def prepare_forecast_inputs(self, df: pd.DataFrame, program_name: str, n_weeks_ago: int = 3, debug=False):
        """
            Строит датасет для программы, добавляет признаки.
        """
        
        # Проверка кэша (убираем all_holidays и work_saturdays из ключа)
        cache_key = (program_name, n_weeks_ago, self.start_date_forecast, self.channel)
        if cache_key in RuleBasedForecaster._prepare_cache:
            if debug:
                print(f"📦 Использую кэш для программы '{program_name}'")
            return RuleBasedForecaster._prepare_cache[cache_key]
        
        columns_order = []
        palomars_last_n_weeks = pd.DataFrame()
        last_n_weeks = pd.DataFrame()

        palomars_history = self.palomars_history.copy()
        palomars_history['Дата'] = pd.to_datetime(palomars_history['Дата'])

        last_fact_date = palomars_history['Дата'].max()

        palomars_history['День недели'] = palomars_history['Дата'].dt.strftime('%A')
        if 'program_name' in palomars_history.columns:
            palomars_history.rename(columns={'program_name': 'Название программы'}, inplace=True)

        df_copy = df.copy()

        data = RuleBasedForecaster.add_duration(df_copy)
        palomars_history = RuleBasedForecaster.add_duration(palomars_history)

        # Используем кэшированные версии с предзагруженными праздниками
        data['Тип дня'] = data['Дата'].apply(
            lambda x: RuleBasedForecaster._get_day_type_cached(
                datetime.strftime(x, '%Y-%m-%d'), 
                RuleBasedForecaster._holidays_tuple, 
                RuleBasedForecaster._work_saturdays_tuple
            )
        )
        palomars_history['Тип дня'] = palomars_history['Дата'].apply(
            lambda x: RuleBasedForecaster._get_day_type_cached(
                datetime.strftime(x, '%Y-%m-%d'), 
                RuleBasedForecaster._holidays_tuple, 
                RuleBasedForecaster._work_saturdays_tuple
            )
        )

        columns = ['Время выхода', 'Время окончания']
        for column in columns:
            data[f'{column}_dt'] = pd.to_datetime(data[column], format='%H:%M:%S')
            palomars_history[f'{column}_dt'] = pd.to_datetime(palomars_history[column], format='%H:%M:%S')

            data['hour'] = pd.to_datetime(data[f'{column}_dt']).dt.hour
            palomars_history['hour'] = pd.to_datetime(palomars_history[f'{column}_dt']).dt.hour

            if column == 'Время выхода':
                data['dt_start'] = data['hour'].apply(RuleBasedForecaster._add_day_part_cached)
                palomars_history['dt_start'] = palomars_history['hour'].apply(RuleBasedForecaster._add_day_part_cached)
            else:
                data['dt_end'] = data['hour'].apply(RuleBasedForecaster._add_day_part_cached)
                palomars_history['dt_end'] = palomars_history['hour'].apply(RuleBasedForecaster._add_day_part_cached)

            data = data.drop([f'{column}_dt', 'hour'], axis=1)
            palomars_history = palomars_history.drop([f'{column}_dt', 'hour'], axis=1)

        if debug:
            print('=============== ЗАПУСКАЮ ДЕБАГГЕР ===============\n')
            print(f'Анализ программы: {program_name}')
        
        if self.channel == 'МатчТВ':
            columns_order = [
                'Дата', 'Название программы', 'Время выхода', 
                'Время окончания', 'Продолжительность',
                'dur_min', 'День недели', 'Тип дня',
                'dt_start', 'dt_end', 'Вид спорта', 'Метка', 'Share'
            ]
        else:
            columns_order = [
                'Дата', 'Название программы', 'Время выхода', 
                'Время окончания', 'Продолжительность',
                'dur_min', 'День недели', 'Тип дня',
                'dt_start', 'dt_end', 'Share'
            ]

        per_forecast = data[data['Share'] == ''].reset_index(drop=True)
        per_forecast = per_forecast[columns_order]
        if debug:
            print(f'Всего требуется спрогнозировать {len(per_forecast)} различных дней-слотов.')
        
        dates_per_forecast = per_forecast['Дата'].unique()
        if debug:
            print(f'Всего требуется спрогнозировать: {len(dates_per_forecast)} уникальных дат.')

        history = data[data['Share'] != ''].reset_index(drop=True)
        history = history[columns_order]

        if self.channel == 'МатчТВ':
            palomars_history = palomars_history[[
                'Дата', 'Название программы', 'Время выхода', 
                'Время окончания', 'Продолжительность', 'Жанр',
                'dur_min', 'День недели', 'Тип дня',
                'dt_start', 'dt_end', 'Вид спорта', 'Метка', 'Share'
            ]]
        else:
            palomars_history = palomars_history[[
                'Дата', 'Название программы', 'Время выхода', 
                'Время окончания', 'Продолжительность', 'Жанр',
                'dur_min', 'День недели', 'Тип дня', 'dt_start', 'dt_end', 'Share'
            ]]

        current_year = pd.DataFrame()
        
        if self.month_num == 1:
            current_year_mask = (history['Дата'] >= f'{self.current_year - 1}-01-01') & \
                                (history['Дата'] < self.start_date_forecast)
            current_year = history[current_year_mask].reset_index(drop=True)

            current_year_mask_palomars = (palomars_history['Дата'] >= f'{self.current_year - 1}-01-01') & \
                                         (palomars_history['Дата'] < self.start_date_forecast)
            palomars_history = palomars_history[current_year_mask_palomars].reset_index(drop=True)
        else:
            current_year_mask = (history['Дата'] >= f'{self.current_year}-01-01') & \
                                (history['Дата'] < self.start_date_forecast)
            current_year = history[current_year_mask].reset_index(drop=True)
            
            current_year_mask_palomars = (palomars_history['Дата'] >= f'{self.current_year}-01-01') & \
                                         (palomars_history['Дата'] < self.start_date_forecast)
            palomars_history = palomars_history[current_year_mask_palomars].reset_index(drop=True)
        
        if len(current_year) != 0:
            if debug:
                print(f"Последняя фактическая дата: {last_fact_date.strftime('%Y-%m-%d')}.")

            date_n_weeks_ago = last_fact_date - pd.Timedelta(weeks=n_weeks_ago)
            if debug:
                print(f"Последние {n_weeks_ago} недели: {date_n_weeks_ago.strftime('%Y-%m-%d')} - {last_fact_date.strftime('%Y-%m-%d')}.")
            
            last_n_weeks = history[history['Дата'] > date_n_weeks_ago].reset_index(drop=True).copy()
            palomars_last_n_weeks = palomars_history[palomars_history['Дата'] > date_n_weeks_ago].reset_index(drop=True).copy()
        else:
            if debug:
                print('Программы ' + Color.BLUE + f"'{program_name}'" + Color.END + ' ещё не было в текущем году.')
                print(f"Последняя фактическая дата в истории Palomars: {last_fact_date.strftime('%Y-%m-%d')}.")
            
            date_n_weeks_ago = last_fact_date - pd.Timedelta(weeks=n_weeks_ago)
            if debug:
                print(f"Последние {n_weeks_ago} недели из Palomars: {date_n_weeks_ago.strftime('%Y-%m-%d')} - {last_fact_date.strftime('%Y-%m-%d')}.")

            last_n_weeks = pd.DataFrame()
            palomars_last_n_weeks = palomars_history[palomars_history['Дата'] > date_n_weeks_ago].reset_index(drop=True).copy()

        program_forecast_package = {
            'target_dates': dates_per_forecast,
            'target_data': per_forecast,
            'history_last_n_weeks': last_n_weeks,
            'palomars_history': palomars_last_n_weeks,
            'full_palomars_history': palomars_history,
        }

        # Сохраняем в кэш
        RuleBasedForecaster._prepare_cache[cache_key] = program_forecast_package
        
        # Ограничиваем размер кэша
        if len(RuleBasedForecaster._prepare_cache) > 100:
            keys_to_remove = list(RuleBasedForecaster._prepare_cache.keys())[:50]
            for key in keys_to_remove:
                del RuleBasedForecaster._prepare_cache[key]
        
        return program_forecast_package
    
    
    def _try_find(self, data, search_values, fields, dur_gap=False, debug=False):
        """
        Проверяет условие и возвращает результат, если есть данные
        
        Параметры:
            data: pd.DataFrame - данные
            search_values: dict - словарь с искомыми значениями
            fields: list - поля для поиска
            dur_gap: bool - флаг люфта
            debug: bool - режим отладки
        """
        data_copy = data.copy()
        
        # Создаем маску на основе search_values
        mask = np.ones(len(data_copy), dtype=bool)
        for field in fields:
            if field in data_copy.columns and field in search_values:
                mask &= (data_copy[field].values == search_values[field])
        
        if not mask.any():
            return 0.0, None, False
        
        filtered_data = data_copy.loc[mask]

        if len(filtered_data) > 1:
            share_mean, used_mask = self.calculate_share(filtered_data, None, parent_df=data)
            if debug:
                print(f'Количество записей в выборке {len(filtered_data)}. Данные были найдены по полям {", ".join(fields)}.')
            return share_mean, used_mask, True
        elif len(filtered_data) == 1:
            if dur_gap == False:
                if debug:
                    print(f'Количество записей в выборке 1. Данные были найдены по полям {", ".join(fields)}.')
                return filtered_data['Share'].iloc[0], None, True
            else:
                return 0.0, None, False
        else:
            return 0.0, None, False
    

    def _search_by_combinations(self, data: pd.DataFrame, whole_history: pd.DataFrame, search_values: dict, debug: bool = False):
        """Внутренний метод для поиска по комбинациям полей."""
        if len(data) == 0:
            raise ValueError(f"Таблица с историей пустая! Не могу построить прогноз.")
        
        sport_type = None
        share_mean = 0.0
        used_mask = None
        found = False
        
        # Тип части дня начала программы
        day_part_start = search_values['dt_start']
        # Тип части дня окончания программы
        day_part_end = search_values['dt_end']
        # Вычленяем длительность программы
        dur_min = search_values['dur_min']

        if self.channel == 'МатчТВ':
            sport_type = search_values.get('Вид спорта')

        # Определяем границы люфта
        dur_min_lower = dur_min * 0.7  # -30%
        dur_min_upper = dur_min * 1.3  # +30%

        # Для канала '2X2' оставляем возможность присутствия нуль
        if self.channel != '2X2':
            if 'Share' not in data.columns:
                raise ValueError(f"Колонка 'Share' отсутствует в данных. Доступные колонки: {data.columns.tolist()}")
            # Отфильтровываем ненулевые значения долей
            data = data[data['Share'].values != 0].reset_index(drop=True)
        
        # Предварительная подготовка данных для ускорения
        # Преобразуем нужные колонки в numpy массивы для быстрого доступа
        data_dur_min = data['dur_min'].values
        data_dt_start = data['dt_start'].values
        data_dt_end = data['dt_end'].values
        if self.channel == 'МатчТВ':
            data_sport = data['Вид спорта'].values if 'Вид спорта' in data.columns else None
        
        # Все стратегии поиска
        strategies = [
            # 1-5 проходы: по комбинациям
            (self.combinations_list_general, False, 'general'),
            (self.combinations_list, False, 'basic'),
            (self.combinations_list_general_without_dur_min, True, 'general_tolerance'),
            (self.combinations_list_without_dur_min, True, 'basic_tolerance'),
            (self.combinations_list_no_duration, True, 'no_duration'),
            # Специальные проходы
            ('special_day_part', None),
            ('special_duration', None),
            ('special_duration_with_gap', None),
        ]
        
        for strategy in strategies:
            if found:
                break
            
            if isinstance(strategy[0], list):  # Обычные комбинации
                combo_list, use_gap, _ = strategy
                
                for fields in combo_list:
                    # Создаем маску с помощью numpy (быстро)
                    mask = np.ones(len(data), dtype=bool)
                    
                    # Проверяем все поля из комбинации
                    all_fields_exist = True
                    for field in fields:
                        if field not in data.columns:
                            all_fields_exist = False
                            break
                        if field not in search_values:
                            all_fields_exist = False
                            break
                    
                    if not all_fields_exist:
                        if debug:
                            print(f"  ❌ Поля {fields} отсутствуют в данных или search_values")
                        continue
                    
                    # Применяем фильтры по каждому полю
                    for field in fields:
                        if field == 'dur_min' and use_gap:
                            continue  # Обработаем отдельно
                        mask &= (data[field].values == search_values[field])
                    
                    # Применяем фильтр по длительности с люфтом если нужно
                    dur_gap = False
                    if use_gap:
                        mask &= (data_dur_min >= dur_min_lower) & (data_dur_min <= dur_min_upper)
                        dur_gap = True
                    elif 'dur_min' in fields:
                        # Если dur_min в полях и без люфта
                        mask &= (data_dur_min == dur_min)
                    
                    if not mask.any():
                        if debug:
                            print(f"  ❌ Нет совпадений по полям: {fields}")
                        continue
                    
                    # Получаем отфильтрованные данные
                    filtered_indices = np.where(mask)[0]
                    filtered_data = data.iloc[filtered_indices]
                    
                    if len(filtered_data) > 1:
                        share_mean, used_mask = self.calculate_share(filtered_data, None, parent_df=data)
                        used_mask = pd.Series(mask, index=data.index)
                        found = True
                        if debug:
                            print(f'✅ Найдено {len(filtered_data)} записей по полям {", ".join(fields)}.')
                        break
                    elif len(filtered_data) == 1 and not dur_gap:
                        share_mean = filtered_data['Share'].iloc[0]
                        used_mask = pd.Series(mask, index=data.index)
                        found = True
                        if debug:
                            print(f'✅ Найдена 1 запись по полям {", ".join(fields)}.')
                        break
            
            else:  # Специальные стратегии
                strategy_name = strategy[0]
                
                # ШЕСТОЙ ПРОХОД: поиск по частям дня
                if strategy_name == 'special_day_part':
                    if debug:
                        print(Color.ITALIC + '🔍 Пробую поиск по типу части дня и длительности ...' + Color.END)
                    
                    # Маска по частям дня
                    day_part_mask = (data_dt_start == day_part_start) & (data_dt_end == day_part_end)
                    
                    # Точное совпадение по длительности
                    final_mask = day_part_mask & (data_dur_min == dur_min)
                    filtered_indices = np.where(final_mask)[0]
                    
                    if len(filtered_indices) > 1:
                        filtered_data = data.iloc[filtered_indices]
                        share_mean, used_mask = self.calculate_share(filtered_data, None, parent_df=data)
                        used_mask = pd.Series(final_mask, index=data.index)
                        found = True
                        if debug:
                            print(f'✅ Найдено {len(filtered_indices)} записей по частям дня и длительности')
                        continue
                    
                    # С люфтом по длительности
                    final_mask = day_part_mask & (data_dur_min >= dur_min_lower) & (data_dur_min <= dur_min_upper)
                    filtered_indices = np.where(final_mask)[0]
                    
                    if len(filtered_indices) > 1:
                        filtered_data = data.iloc[filtered_indices]
                        share_mean, used_mask = self.calculate_share(filtered_data, None, parent_df=data)
                        used_mask = pd.Series(final_mask, index=data.index)
                        found = True
                        if debug:
                            print(f'✅ Найдено {len(filtered_indices)} записей по частям дня с люфтом')
                        continue
                    
                    # Только по частям дня
                    filtered_indices = np.where(day_part_mask)[0]
                    
                    if len(filtered_indices) > 1:
                        filtered_data = data.iloc[filtered_indices]
                        unique_durations = np.unique(filtered_data['dur_min'].values)
                        deltas = np.abs(unique_durations - dur_min)
                        min_idx = np.argmin(deltas)
                        min_key = unique_durations[min_idx]
                        
                        if (min_key >= 0.5 * dur_min) and (min_key <= 1.5 * dur_min):
                            final_mask = day_part_mask & (data_dur_min == min_key)
                            filtered_indices = np.where(final_mask)[0]
                            filtered_data = data.iloc[filtered_indices]
                            if len(filtered_data) > 1:
                                share_mean = np.median(filtered_data['Share'].values)
                                used_mask = pd.Series(final_mask, index=data.index)
                                found = True
                                if debug:
                                    print(f'✅ Найдено {len(filtered_data)} записей по частям дня')
                                continue
                
                # СЕДЬМОЙ ПРОХОД: поиск по длительности и спорту
                elif strategy_name == 'special_duration':
                    # Для МатчТВ пробуем поиск по спорту
                    if self.channel == 'МатчТВ' and sport_type is not None:
                        if debug:
                            print(Color.ITALIC + '🔍 Пробую поиск по виду спорта ...' + Color.END)
                        
                        share_mean = self._search_by_sport(data, sport_type, debug=debug)
                        if share_mean == 0.0:
                            share_mean = self._search_by_sport(whole_history, sport_type, debug=debug)
                        
                        if share_mean != 0.0:
                            found = True
                            used_mask = None
                            if debug:
                                print(f'✅ Найдено по спорту: {share_mean:.4f}')
                            continue
                    
                    # Поиск по длительности
                    if debug:
                        print(Color.ITALIC + '🔍 Пробую поиск по длительности ...' + Color.END)
                    
                    final_mask = (data_dur_min == dur_min)
                    filtered_indices = np.where(final_mask)[0]
                    
                    if len(filtered_indices) > 1:
                        filtered_data = data.iloc[filtered_indices]
                        share_mean = np.median(filtered_data['Share'].values)
                        used_mask = pd.Series(final_mask, index=data.index)
                        found = True
                        if debug:
                            print(f'✅ Найдено {len(filtered_indices)} записей по длительности')
                        continue
                
                # ВОСЬМОЙ ПРОХОД: поиск по длительности с люфтом
                elif strategy_name == 'special_duration_with_gap':
                    if debug:
                        print(Color.ITALIC + '🔍 Пробую поиск по длительности с люфтом ±30% ...' + Color.END)
                    
                    duration_backlash_mask = (data_dur_min >= dur_min_lower) & (data_dur_min <= dur_min_upper)
                    
                    # Для МатчТВ пробуем с видом спорта
                    if self.channel == 'МатчТВ' and sport_type is not None and data_sport is not None:
                        final_mask = duration_backlash_mask & (data_sport == sport_type)
                        filtered_indices = np.where(final_mask)[0]
                        
                        if len(filtered_indices) > 1:
                            filtered_data = data.iloc[filtered_indices]
                            share_mean = np.median(filtered_data['Share'].values)
                            used_mask = pd.Series(final_mask, index=data.index)
                            found = True
                            if debug:
                                print(f'✅ Найдено {len(filtered_indices)} записей по длительности с люфтом и спорту')
                            continue
                    
                    # Только по длительности с люфтом
                    filtered_indices = np.where(duration_backlash_mask)[0]
                    
                    if len(filtered_indices) > 1:
                        filtered_data = data.iloc[filtered_indices]
                        share_mean = np.median(filtered_data['Share'].values)
                        used_mask = pd.Series(duration_backlash_mask, index=data.index)
                        found = True
                        if debug:
                            print(f'✅ Найдено {len(filtered_indices)} записей по длительности с люфтом')
                        continue
        
        if not found:
            if debug:
                print('❌ Не удалось найти подходящую выборку ни в одном из проходов.')
            return 0.0, None

        return share_mean, used_mask
    
    
    def _search_by_sport(self, palomars_history: pd.DataFrame, sport_type: str, debug: bool = False):
        """
            Метод для прогнозирования, опираясь на "Вид спорта" для канала МатчТВ.
        """
        share_mean = 0.0
    
        # Формируем дату для фильтрации
        if self.month_num == 1:
            date_threshold = f'{self.current_year - 1}-01-01'
        else:
            date_threshold = f'{self.current_year}-01-01'
        
        # Преобразуем в datetime64 для корректного сравнения
        date_threshold_dt = pd.to_datetime(date_threshold)
        
        # Фильтрация с использованием pandas (не .values для datetime)
        hist = palomars_history[palomars_history['Дата'] >= date_threshold_dt]
        
        if len(hist) == 0:
            raise ValueError('Таблица с историей пустая. Проверьте входные данные.')

        # Для сравнения строк можно использовать .values
        target_sport_data = hist[hist['Вид спорта'].values == sport_type]
        
        if len(target_sport_data) > 0:
            if debug:
                print('🔍 В качестве прогноза беру медиану по выбранному спорту за всю историю ...')
            share_mean = np.median(target_sport_data['Share'].values)

        return share_mean
    

    def forecast_big(self, program_name, date, search_values: dict, last_n_weeks: pd.DataFrame, 
                     palomars_last_n_weeks: pd.DataFrame, palomars_history: pd.DataFrame, debug=False):
        """
            Метод для прогнозирования программ с богатой историей.
        """
        share_mean = 0.0
        used_mask = None

        if len(last_n_weeks) != 0:
            if debug:
                print('\n')
                print(Color.GREEN + 'СТРОЮ ПРОГНОЗ, ОПИРАЯСЬ НА ИСТОРИИ ВЫБРАННОЙ ПРОГРАММЫ ЗА ПОСЛЕДНИЕ N НЕДЕЛЬ...' + Color.END)
                print('\n')

            share_mean, used_mask = self._search_by_combinations(last_n_weeks, palomars_history, search_values, debug=debug)

            if np.isclose(share_mean, 0.0):
                if debug:
                    print('\n')
                    print(Color.GREEN + 'ПЕРЕХОЖУ К ПОИСКУ В ИСТОРИЧЕСКОЙ СЕТКЕ...' + Color.END)
                    print('\n')
                
                share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug=debug)
        
        else:
            if self.channel == 'МатчТВ' and search_values.get('Вид спорта') is not None:
                if debug:
                    print(Color.NAVY + f"Нет истории для программы {program_name} в {date}. Пробуем найти по спорту..." + Color.END)
                
                share_mean = self._search_by_sport(palomars_history, search_values['Вид спорта'], debug=debug)

                if share_mean == 0:
                    if debug:
                        print(Color.NAVY + 'Опираюсь на историческую сетку без упора на программу.' + Color.END)
                    share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug=debug)
            else:
                if debug:
                    print(Color.NAVY + f'Нет истории для программы {program_name} в {date}. Опираюсь на историческую сетку.' + Color.END)
                share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug=debug)

        if np.isclose(share_mean, 0.0):
            if debug:
                print('🔍 В качестве прогноза беру медиану за последние N недель ...')
            
            if len(last_n_weeks) == 0:
                share_mean = np.median(list(palomars_last_n_weeks['Share']))
            else:
                share_mean = np.median(list(last_n_weeks['Share']))
        
        return share_mean, used_mask
    

    def forecast_small(self, search_values: dict, last_n_weeks: pd.DataFrame, 
                       palomars_last_n_weeks: pd.DataFrame, palomars_history: pd.DataFrame, debug=False):
        """
            Метод для прогнозирования программ с маленькой историей.
        """
        share_mean = 0.0
        used_mask = None
        
        if len(last_n_weeks) == 0:
            if debug:
                print('Истории по текущему году нет. Использую историю Palomars.')
            
            share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug=debug)
            
            if np.isclose(share_mean, 0.0):
                if debug:
                    print('🔍 В качестве прогноза беру медиану за последние N недель ...')
                share_mean = np.median(palomars_last_n_weeks['Share'])
                used_mask = None
        else:
            if debug:
                print(f'История по текущему году есть ({len(last_n_weeks)} записей).')
            
            share_mean, used_mask = self._search_by_combinations(last_n_weeks, palomars_history, search_values, debug=debug)
            
            if np.isclose(share_mean, 0.0):
                if debug:
                    print('Не удалось найти совпадений. Пробуем в истории Palomars.')
                
                share_mean, used_mask = self._search_by_combinations(palomars_last_n_weeks, palomars_history, search_values, debug=debug)
                
                if np.isclose(share_mean, 0.0):
                    if debug:
                        print('🔍 В качестве прогноза беру медиану за последние N недель ...')
                    share_mean = np.median(palomars_last_n_weeks['Share'])
                    used_mask = None
        
        return share_mean, used_mask


    def forecast_new(self, search_values: dict, palomars_last_n_weeks: pd.DataFrame, 
                     palomars_history: pd.DataFrame, debug=False):
        """
            Метод для прогнозирования НОВЫХ программ.
        """
        found = False
        sport_type = None

        dur_min = search_values['dur_min']
        dur_min_lower = dur_min * 0.7
        dur_min_upper = dur_min * 1.3

        share_mean = 0.0
        used_mask = None

        if self.channel == 'МатчТВ':
            sport_type = search_values.get('Вид спорта')
        
        # Используем кэшированные комбинации (уже есть в self из _initialize_combinations)
        combinations_list_general = self.combinations_list_general_for_new_programs
        combinations_list_no_duration = self.combinations_list_for_new_programs_no_duration
        
        # Предварительная подготовка numpy массивов для ускорения
        palomars_dur_min = palomars_last_n_weeks['dur_min'].values
        if self.channel == 'МатчТВ' and 'Вид спорта' in palomars_last_n_weeks.columns:
            palomars_sport = palomars_last_n_weeks['Вид спорта'].values
        else:
            palomars_sport = None
        
        # ========== ПЕРВЫЙ ПРОХОД: БЕЗ ЛЮФТА ==========
        if debug:
            print(Color.VIOLET + 'Строю прогноз, опираясь на список комбинаций с обязательным параметром "dur_min".' + Color.END)

        for fields in combinations_list_general:
            # Создаем маску с помощью numpy (быстро)
            mask = np.ones(len(palomars_last_n_weeks), dtype=bool)
            
            for field in fields:
                if field in palomars_last_n_weeks.columns and field in search_values:
                    if field == 'dur_min':
                        mask &= (palomars_dur_min == search_values[field])
                    elif field == 'Вид спорта' and palomars_sport is not None:
                        mask &= (palomars_sport == search_values.get(field))
                    else:
                        mask &= (palomars_last_n_weeks[field].values == search_values.get(field))
            
            if not mask.any():
                if debug:
                    print(f"  ❌ Нет совпадений по полям: {fields}")
                continue
            
            filtered_data = palomars_last_n_weeks.iloc[mask]

            if len(filtered_data) <= self.SMALL_SAMPLE_SIZE:
                if len(filtered_data) == self.EXACT_MATCH_SIZE:
                    share_mean, used_mask = self.calculate_share(filtered_data, None, parent_df=palomars_last_n_weeks)
                    found = True
                    if debug:
                        print(f"  📊 Выборка маленькая. Использую точное среднее по {len(filtered_data)} записям: {share_mean:.4f}")
                        print(f"  ✅ Нашёл совпадения по полям: {fields}")
                else:
                    share_mean = RuleBasedForecaster.get_clean_mean(filtered_data['Share'])
                    used_mask = pd.Series(mask, index=palomars_last_n_weeks.index)
                    found = True
                    if debug:
                        print(f"  📊 Выборка мала ({len(filtered_data)} записей), среднее: {share_mean:.4f}")
                        print(f"  ✅ Нашёл совпадения по полям: {fields}")
                break
            
            else:  # Большая выборка
                duration_mask = filtered_data['dur_min'].values >= dur_min
                duration_filtered = filtered_data.iloc[duration_mask].reset_index(drop=True)
                
                if debug:
                    print(f"  📊 Выборка большая. Фильтрация по длительности ≥ {dur_min} мин: найдено {len(duration_filtered)} записей")
                
                if len(duration_filtered) > 0:
                    combined_mask = pd.Series(mask, index=palomars_last_n_weeks.index)
                    valid_indices = filtered_data.index[duration_mask]
                    combined_mask[~combined_mask.index.isin(valid_indices)] = False
                    
                    share_mean, used_mask = self.calculate_share(duration_filtered, combined_mask, parent_df=palomars_last_n_weeks)
                    found = True
                    
                    if debug and not np.isclose(share_mean, 0.0):
                        print(f"  📈 Рассчитано среднее: {share_mean:.4f}")
                    break
        
        # ========== ВТОРОЙ ПРОХОД: С ЛЮФТОМ ==========
        if not found and np.isclose(share_mean, 0.0):

            if self.channel == 'МатчТВ' and search_values.get('Вид спорта') is not None:
                if debug:
                    print(Color.NAVY + f"Пробуем найти историю по спорту '{search_values['Вид спорта']}'..." + Color.END)
                
                share_mean = self._search_by_sport(palomars_history, search_values['Вид спорта'], debug=debug)
                if share_mean != 0.0:
                    found = True
            
                if not found:
                    if debug:
                        print(Color.VIOLET + 'Добавляю люфт ±30% в длительность...' + Color.END)

                    duration_backlash_mask = (palomars_dur_min >= dur_min_lower) & (palomars_dur_min <= dur_min_upper)
                    
                    for fields in combinations_list_no_duration:
                        # Создаем маску с помощью numpy
                        mask = np.ones(len(palomars_last_n_weeks), dtype=bool)
                        
                        for field in fields:
                            if field in palomars_last_n_weeks.columns and field in search_values:
                                if field == 'Вид спорта' and palomars_sport is not None:
                                    mask &= (palomars_sport == search_values.get(field))
                                else:
                                    mask &= (palomars_last_n_weeks[field].values == search_values.get(field))
                        
                        if not mask.any():
                            if debug:
                                print(f"  ❌ Нет совпадений по полям: {fields}")
                            continue

                        final_mask = mask & duration_backlash_mask

                        if not final_mask.any():
                            if debug:
                                print(f"  ❌ Нет совпадений с люфтом по полям {fields}")
                            continue

                        filtered_data = palomars_last_n_weeks.iloc[final_mask]

                        if len(filtered_data) > 1:
                            if len(filtered_data) >= self.EXACT_MATCH_SIZE:
                                share_mean, used_mask = self.calculate_share(filtered_data, None, parent_df=palomars_last_n_weeks)
                                found = True
                                if debug:
                                    print(f"  📊 Использую точное среднее по {len(filtered_data)} записям: {share_mean:.4f}")
                                    print(f"  ✅ Нашёл совпадения по полям: {fields}")
                            else:
                                share_mean = RuleBasedForecaster.get_clean_mean(filtered_data['Share'])
                                used_mask = pd.Series(final_mask, index=palomars_last_n_weeks.index)
                                found = True
                                if debug:
                                    print(f"  📊 Выборка мала ({len(filtered_data)} записей), среднее: {share_mean:.4f}")
                                    print(f"  ✅ Нашёл совпадения по полям: {fields}")
                            break
            
            else:
                if debug:
                    print(Color.VIOLET + 'Добавляю люфт ±30% в длительность...' + Color.END)

                duration_backlash_mask = (palomars_dur_min >= dur_min_lower) & (palomars_dur_min <= dur_min_upper)
                
                for fields in combinations_list_no_duration:
                    # Создаем маску с помощью numpy
                    mask = np.ones(len(palomars_last_n_weeks), dtype=bool)
                    
                    for field in fields:
                        if field in palomars_last_n_weeks.columns and field in search_values:
                            mask &= (palomars_last_n_weeks[field].values == search_values.get(field))
                    
                    if not mask.any():
                        if debug:
                            print(f"  ❌ Нет совпадений по полям: {fields}")
                        continue

                    final_mask = mask & duration_backlash_mask

                    if not final_mask.any():
                        if debug:
                            print(f"  ❌ Нет совпадений с люфтом по полям {fields}")
                        continue

                    filtered_data = palomars_last_n_weeks.iloc[final_mask]

                    if len(filtered_data) > 1:
                        if len(filtered_data) >= self.EXACT_MATCH_SIZE:
                            share_mean, used_mask = self.calculate_share(filtered_data, None, parent_df=palomars_last_n_weeks)
                            found = True
                            if debug:
                                print(f"  📊 Использую точное среднее по {len(filtered_data)} записям: {share_mean:.4f}")
                                print(f"  ✅ Нашёл совпадения по полям: {fields}")
                        else:
                            share_mean = RuleBasedForecaster.get_clean_mean(filtered_data['Share'])
                            used_mask = pd.Series(final_mask, index=palomars_last_n_weeks.index)
                            found = True
                            if debug:
                                print(f"  📊 Выборка мала ({len(filtered_data)} записей), среднее: {share_mean:.4f}")
                                print(f"  ✅ Нашёл совпадения по полям: {fields}")
                        break

        # Если ничего не нашли - пробуем поиск по длительности
        if not found and np.isclose(share_mean, 0.0):
            if debug:
                print("Пробую поиск по длительности")
            share_mean, used_mask = self.find_by_duration(palomars_last_n_weeks, dur_min, use_tolerance=False)
            
        if np.isclose(share_mean, 0.0):
            share_mean, used_mask = self.find_by_duration(palomars_last_n_weeks, dur_min, use_tolerance=True)
            if debug:
                print(f"  🔄 Ищу с люфтом ±30%")
                print(f"  📈 Рассчитано среднее: {share_mean:.4f}")
        
        # Если ничего не нашли - берем медиану
        if np.isclose(share_mean, 0.0):
            if debug:
                print('🔍 В качестве прогноза беру медиану за последние N недель ...')
            share_mean = np.median(palomars_last_n_weeks['Share'].values)
        
        return share_mean, used_mask
    
    
    def build_forecast_per_group(
            self,
            programs_dict: dict,
            volume_flag: str,
            n_weeks_ago: int,
            debug = False
        ):
        """
            Метод для построения прогноза.
            Параметры:
            ----------
                programs_dict: dict 
                    Словарь с историей программ. 
                    Ключ - название программы, значение - датафрейм с историей, а также датами, которые нужно спрогнозировать.
                volume_flag: str
                    Флаг, который будет указывать, какой метод для прогнозирования необходимо применять.
                n_weeks_ago: int
                    Количество последних недель, которое берётся для анализа
        """
        if volume_flag not in ['small', 'big', 'new']:
            raise ValueError(f"Флаг '{volume_flag}' не существует. Выберите из списка: {['small', 'big', 'new']}")
        
        if volume_flag == 'big':
            print(Color.BOLD + f'==== Прогнозирую крупные программы ====' + Color.END)
        
        elif volume_flag == 'small':
            print(Color.BOLD + f'==== Прогнозирую мелкие программы ====' + Color.END)
        
        elif volume_flag == 'new':
            print(Color.BOLD + f'==== Прогнозирую новые программы ====' + Color.END)

        results_per_program = {}
        all_masks = {}

        last_n_weeks = pd.DataFrame()
        palomars_last_n_weeks = pd.DataFrame()

        for program_name in programs_dict.keys():
        
            df = programs_dict[program_name].reset_index(drop=True)

            # Подготовка данных для прогнозирования (убрали параметры праздников)
            dict_analysis = self.prepare_forecast_inputs(
                df, program_name, 
                n_weeks_ago=n_weeks_ago, 
                debug=debug
                )

            last_n_weeks = dict_analysis['history_last_n_weeks']
            palomars_last_n_weeks = dict_analysis['palomars_history']
            full_history = dict_analysis['full_palomars_history']

            if debug:
                print(f'==== ПОСТРОЕНИЕ ПРОГНОЗА ДЛЯ ПРОГРАММЫ {program_name} ====')
            
            forecast_results = []
            date_masks = {}
            for date in dict_analysis['target_dates']:
                if debug:
                    print(f'------------- Прогнозирую дату {date} -------------')

                # Отбор даты, которую собираемся спрогнозировать
                per_forecast = dict_analysis['target_data']
                future_df = per_forecast[per_forecast['Дата'] == date].reset_index(drop=True)

                future_df['program_type'] = None
                
                share_forecast = 0.0
                
                # Итерируемся по выделенным данным
                for i in range(len(future_df)):
                    sport_type = None
                    specific_flag = None

                    time_start = future_df.iloc[i]['Время выхода']
                    dur_min = future_df.iloc[i]['dur_min']
                    day_of_week = future_df.iloc[i]['День недели']
                    day_type = future_df.iloc[i]['Тип дня']
                    dt_start = future_df.iloc[i]['dt_start']
                    dt_end = future_df.iloc[i]['dt_end']

                    if self.channel == 'МатчТВ':
                        sport_type = future_df.iloc[i]['Вид спорта']
                        specific_flag = future_df.iloc[i]['Метка']
            
                    if debug:
                        print(f'Для {date} буду искать следующие кейсы в истории:')
                        print('\n')
                        print(f' - Время выхода:                            {time_start}')
                        print(f' - Продолжительность:                       {dur_min} мин')
                        print(f' - День недели:                             {day_of_week}')
                        print(f' - Тип дня:                                 {day_type}')
                        print(f' - Тип части суток начала программы:        {dt_start}')
                        print(f' - Тип части суток окончания программы:     {dt_end}')
                        # Добавляем дополнительный вывод параметров, если канал "МатчТВ"
                        if self.channel == 'МатчТВ':
                            print(f' - Вид спорта:                           {sport_type}')
                            print(f' - Метка:                                   {specific_flag}')

                        print('\n')
                
                    # Шаг 2. Задаем значения для поиска
                    # Значения для поиска
                    search_values = {
                        'Время выхода': time_start,
                        'dur_min': dur_min,
                        'День недели': day_of_week,
                        'Тип дня': day_type,
                        'dt_start': dt_start,
                        'dt_end': dt_end
                    }
                    # Добавляем дополнительные параметры, если канал "МатчТВ"
                    if self.channel == 'МатчТВ':
                        search_values['Вид спорта'] = sport_type
                        search_values['Метка'] = specific_flag

                    # Построение прогноза для крупной программы
                    if volume_flag == 'big':
                        share_forecast, mask = self.forecast_big(
                            program_name, date, search_values, 
                            last_n_weeks, palomars_last_n_weeks,
                            full_history, debug=debug
                        )

                    # Построение прогноза для мелкой программы
                    elif volume_flag == 'small':
                        share_forecast, mask = self.forecast_small(
                            search_values, last_n_weeks, palomars_last_n_weeks,
                            full_history, debug=debug
                        )
                    
                    # Построение прогноза для НОВОЙ программы
                    elif volume_flag == 'new':
                        share_forecast, mask = self.forecast_new(
                            search_values, palomars_last_n_weeks,
                            full_history, debug=debug)

                    date_masks[date.strftime('%Y-%m-%d')] = mask
            
                    # Запись прогнозного значения в ячейку
                    future_df.at[i, 'Share'] = share_forecast
                    future_df.at[i, 'program_type'] = volume_flag
            
                forecast_results.append(future_df)
                
                if debug:
                    print('-' * 50)
                    print('\n')
                    
                all_masks[program_name] = date_masks
                
            results_per_program[program_name] = pd.concat(forecast_results).reset_index(drop=True)
        return results_per_program
    
    
    def pipeline_forecaster(
            self, 
            all_programs_to_forecast: dict, 
            fact_part_of_month: pd.DataFrame,
            n_weeks_ago: int,
            debug = False
        ):
        """
            Пайплайн для построения прогноза совокупно для всех трёх групп: "big", "small", "new".
            Параметры:
            ----------
                all_programs_to_forecast: dict of dicts
                    Словарь словарей с разбивкой по группам программ: "big", "small", "new".
                    Ключ - один из трёх групп: "big", "small", "new"
                    Значение: словарь из программ с историей. Ключ - название программы, Значение - таблица с историей
                fact_part_of_month: pd.DataFrame
                    Таблица с фактической частью месяца
                n_weeks_ago: int
                    Количество последних недель, которое берётся для анализа
        """
        if debug:
            if len(fact_part_of_month) != 0:
                print('Есть накопленный факт. Учитываю это при прогнозировании.')
            else:
                print('Накопленного факта нет. Прогнозирую весь месяц целиком.')

        # Словарь с результатми прогнозов
        forecast_results = {}
        for key, programs_dict in all_programs_to_forecast.items():
            forecast_results[key] = self.build_forecast_per_group(
                                                        programs_dict, 
                                                        key,  # volume_flag
                                                        n_weeks_ago,
                                                        debug = debug
                                                    )

        results = []
        for key, result_dict in forecast_results.items():
            for program, forecast in result_dict.items():
                results.append(forecast)

        # Итоговая таблица с прогнозом
        data_full = pd.concat(results).reset_index(drop = True)

        columns = ['Дата', 'Название программы', 'Время выхода', 'Время окончания', 'Share', 'program_type']
        data_full = data_full[columns]

        #forecast_df = pd.DataFrame()

        # Если есть накопленный факт, то мы соединяем между собой две таблицы
        #if len(fact_part_of_month) != 0:

            # Добавляем колонку program_type, если её нет
        #    if 'program_type' not in fact_part_of_month.columns:
        #        fact_part_of_month['program_type'] = 'FACT'

        #    fact_part_of_month = fact_part_of_month[columns]
        #    forecast_df = pd.concat([fact_part_of_month, data_full]).reset_index(drop = True)
        #else:
        forecast_df = data_full

        forecast_df['Дата'] = pd.to_datetime(forecast_df['Дата'])
        sorted_webs = forecast_df.sort_values('Дата').reset_index(drop = True)

        dates_unique = sorted_webs['Дата'].unique()
        res = []
        for date in dates_unique:
            date_dt = pd.to_datetime(date)
            t = sorted_webs[sorted_webs['Дата'] == date_dt]
            t['sort_key'] = t['Время выхода'].apply(BaseParser.get_sort_key)
            final = t.sort_values('sort_key').reset_index(drop = True)
            final = final.drop('sort_key', axis=1)
            res.append(final)

        general_result = pd.concat(res).reset_index(drop = True)
        general_result['Дата'] = general_result['Дата'].dt.strftime('%Y-%m-%d')
        # Расставляем колонки в нужном порядке
        general_result = general_result[columns]
        return general_result
