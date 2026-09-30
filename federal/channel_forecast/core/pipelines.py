import os
import numpy as np
import pandas as pd
from typing import Optional
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import warnings
warnings.filterwarnings('ignore')

from OMA_tools.io_data.colors import *
from OMA_tools.federal.channel_forecast.grid_preprocessing import *
from OMA_tools.federal.channel_forecast.core.simple_models import *
from OMA_tools.federal.channel_forecast.support import Assistant



class ChannelAnalysisMaster:
    """
        Класс, в котором реализованы пайплайны для выгрузки данных из БД Mediascope, 
        а также обновление исторической сетки VIMB, освовываясь на отчете Размещение/Сводная таблица.

        !!! В А Ж Н О !!!
        Класс работает только для конкретного канала!
    """
    def __init__(
            self,
            channel: str,
            date_filter: list,
            company_filter: str,
            basedemo_filter: str,
            matched_grid_file: str,
            vocabulary_file: str,
            cities_file: str,
            auedience_file: Optional[str] = None,           # опциональный параметр
            web_file: Optional[str] = None,                 # опциональный параметр
            weighted_share_file: Optional[str] = None,      # опциональный параметр
            hist_vimb_file: Optional[str] = None            # опциональный параметр
    ):

        """
            Атрибуты класса (Можно передавать только нужные параметры)
                auedience_file: str: 
                    Полный путь к файлу с Total TV Auedience для какого-то конкретного канала.
                web_file: str: 
                    Полный путь к файлу с исторической сеткой Mediascope для какого-то конкретного канала.
                weighted_share_file: str: 
                    Полный путь к файлу со взвешенной долей и исторической сеткой Mediascope для какого-то конкретного канала.
                hist_vimb_file: str
                    Полный путь к файлу с исторической сеткой VIMB для какого-то конкретного канала.
                matched_grid_file: str
                    Полный путь к файлу со смэтченной исторической сеткой VIMB-Palomars для какого-то конкретного канала.
                vocabulary_file: str
                    Полный путь к файлу - справочнику по выбранному каналу.
                cities_file: str
                    Полный путь к файлу - справочнику с городами некоторых стран. (!!! Нужен для МатчТВ !!!)
            
        """
        self.channel = channel
        self.date_filter = date_filter
        self.company_filter = company_filter
        self.basedemo_filter = basedemo_filter
        
        # Файлы опциональны - передаем только те, что нужны
        self.auedience_file = auedience_file
        self.web_file = web_file
        self.weighted_share_file = weighted_share_file
        self.hist_vimb_file = hist_vimb_file
        self.matched_grid_file = matched_grid_file
        self.vocabulary_file = vocabulary_file
        self.cities_file = cities_file
        
        # Кэшируем результаты
        self._total_tv_auedience = None
        self._web_df = None

        self.STOP_WORDS = ['погода', 'межпрограммные заставки']
        self.PATTERN = '|'.join(self.STOP_WORDS)

        # Справочник с особенными названиями для сопоставления программ по выбранному каналу
        self.vocabulary = pd.read_excel(self.vocabulary_file, sheet_name=f'{self.channel}')
    

    def auedience_pipeline(self):
        """
            Пайплайн для выгрузки и записи Auedience в файл для какого-то конкретного Федерального канала и конкретной БЦА.
        """
        # Если файл не передан, то выгрузка Total Channels Auedience не произойдет.
        if not self.auedience_file:
            raise ValueError('🚨 Для выполнения Auedience пайплайна необходимо указать auedience_file')

        A_parser = AuedienceParser(self.auedience_file)

        # 1. Выгрузка новых данных по Total TV Auedience
        auedience_new = A_parser.auedience_by_slots(self.date_filter, self.company_filter, self.basedemo_filter)

        # 2. Обновление таблицы
        print('🔄 Обновляю файл с Total TV Auedience. Пожалуйста, подождите ...')
        self.total_tv_auedience = A_parser.update_table_auedience(auedience_new)

        # 3. Сохранение в файл
        print('✅ Сохраняю файл с Total TV Auedience. Пожалуйста, подождите ...')
        A_parser.make_style_of_auedience_table(self.total_tv_auedience, 'Sheet1')

        return self.total_tv_auedience
    

    def mediascope_web_pipeline(self):
        """
            Пайплайн для выгрузки и записи исторической сетки Mediascope в файл для какого-то конкретного Федерального канала и конкретной БЦА.
        """
        # Если файл не передан, то выгрузка сетки Palomers не произойдет.
        if not self.web_file:
            raise ValueError('🚨 Для выполнения web пайплайна необходимо указать web_file')

        plmrs_parser = MediascopeParser(self.channel, self.web_file)
        # 1. Выгрузка новых исторических данных
        web_new = plmrs_parser.make_web(self.date_filter, self.company_filter, self.basedemo_filter)

        web_new = web_new[~web_new['Название программы'].str.contains(self.PATTERN, case=False, na=False)]

        # 2. Обновление таблицы
        print('🔄 Обновляю файл с исторической сеткой Mediascope. Пожалуйста, подождите ...')
        self.web_df = plmrs_parser.update_web_table(web_new)

        # 3. Сохранение в файл
        print('✅ Сохраняю файл с исторической сеткой Mediascope. Пожалуйста, подождите ...')
        plmrs_parser.make_style_of_web_table(self.web_df, 'Sheet1')

        # Если нужно вернуть в строковый формат
        web_new['Дата'] = web_new['Дата'].dt.strftime('%Y-%m-%d')
        web_new['Время выхода'] = web_new['Время выхода'].dt.strftime('%H:%M:%S')
        web_new['Время окончания'] = web_new['Время окончания'].dt.strftime('%H:%M:%S')

        return web_new, self.web_df
    

    def plmrs_web_pipeline(
                self,
                web_new,
                start_time_col: str = 'Время выхода', 
                end_time_col: str = 'Время окончания',
                date_col: str = 'Дата'):
        """
            Пайплайн для обновления и записи в файл рассчитанных взвешенных долей для какого-то конкретного Федерального канала и конкретной БЦА.
        """
        # Если файл не передан, то расчет взвешенных долей не будет реализован.
        if not self.weighted_share_file:
            raise ValueError('🚨 Для выполнения web пайплайна необходимо указать weighted_share_file')

        parser = TVPreprocessing(self.channel, self.weighted_share_file, web_new)

        print('📈 Считаю взвешенную долю. Пожалуйста, подождите ...')
        # 1. Расчет взвешенной доли
        new_df, shares = parser.process_daily_weighted_shares(self.total_tv_auedience)

        # 2. Обновление таблицы
        new_df['Дата'] = pd.to_datetime(new_df['Дата'])
        updated = MediascopeParser(self.channel, self.weighted_share_file).update_web_table(new_df)

        # 3. Сохранение в файл
        print('🔄 Обновляю файл со взвешенной долей и исторической сеткой Mediascope. Пожалуйста, подождите ...')
        parser.make_plmrs_style_of_table(updated, 'Sheet1')

        return updated
    

    def matched_grids_pipeline(self, start_date: str, stop_date: str):
        """
            Пайплайн для объединения сеток VIMB и Palomars между собой
            Параметры:
            ----------
                start_date: str
                    Дата, начиная с которой начинаем обновлять фактические данные в файле.
                stop_date: str
                    Дата, до которой будем обновлять фактические данные в файле.
        """   
        # 1. Чтение данных с сеткой Mediascope
        palomars = pd.read_excel(self.weighted_share_file)

        # 2. Чтение данных с сеткой VIMB
        vimb = pd.read_excel(self.hist_vimb_file)
        
        # 3. Отбираем период из исторической сетки Palomars, для которого будем производить преобразования.
        plmrs = palomars[(palomars['Дата'] >= start_date) & (palomars['Дата'] <= stop_date)].reset_index(drop=True)
        vimb = vimb[(vimb['Дата'] >= start_date) & (vimb['Дата'] <= stop_date)].reset_index(drop=True)

        # 4. Реализация процесса сопоставления сеток
        matcher = ProgramMatcher(self.channel, self.vocabulary, self.matched_grid_file, plmrs, vimb)
        result_webs, not_matched = matcher.match_vimb_with_palomars_grids(self.cities_file)

        # 5. Обновление файла с фактическими данными по выбранному каналу
        matcher.update_file(result_webs, 'Sheet1')


    def unified_pipeline(self, run_all: bool = True, **kwargs):
        """
        Гибкий объединенный пайплайн.
        
        Args:
            run_all: Если True, запускает все доступные пайплайны
            **kwargs: Можно передать какие пайплайны запускать:
                      run_auedience = True/False, run_web = True/False, 
                      run_plmrs = True/False, run_matched = True/False, 
                      matched_start_date = None, matched_stop_date = None  # Обязательные для matched пайплайна!
        """
        results = {}
        
        # Определяем, какие пайплайны запускать
        run_auedience = kwargs.get('run_auedience', run_all or bool(self.auedience_file))
        run_web = kwargs.get('run_web', run_all or bool(self.web_file))
        run_plmrs = kwargs.get('run_plmrs', run_all or bool(self.weighted_share_file))
        run_matched = kwargs.get('run_matched', run_all or (self.matched_grid_file and self.hist_vimb_file))
        
        print(Color.BOLD + f'🚀 Начинаю расчет для канала {self.channel}' + Color.END)

        # 1. Audience пайплайн
        if run_auedience and self.auedience_file:
            print(Color.BOLD + Color.VIOLET + '=== 🎬 Запуск выгрузки Total Channels Auedience пайплайна ===' + Color.END)
            results['auedience'] = self.auedience_pipeline()
        
        # 2. Web пайплайн
        if run_web and self.web_file:
            print(Color.BOLD + Color.BLUE + '=== 🌐 Запуск выгрузки сетки Mediascope пайплайна ===' + Color.END)
            results['web'] = self.mediascope_web_pipeline()
        
        # 3. PLMRS пайплайн (требует audience и web)
        if run_plmrs and self.weighted_share_file:
            if 'web' in results and results['web'] is not None:
                if 'auedience' in results and results['auedience'] is not None:
                    print(Color.BOLD + Color.ORANGE + '=== ⚖️ Запуск расчета взвешенных долей ===' + Color.END)
                    web_new, _ = results['web']
                    results['weighted_shares'] = self.plmrs_web_pipeline(web_new)
                else:
                    # Проверяем существование файла с Total TV Auedience. Без этого не можем продолжить!
                    if not os.path.exists(self.auedience_file):
                        print(Color.BOLD + Color.RED + f'❌ Ошибка: файл c Total TV Auedience для канала {self.channel} не найден: {self.auedience_file}' + Color.END)
                        print('⏭️ Пропускаем PLMRS пайплайн: требуется файл аудитории')
                    else:
                        self.total_tv_auedience = pd.read_excel(self.auedience_file)
                        print(Color.BOLD + Color.GREEN + f'Файл c Total TV Auedience для канала {self.channel} найден!' + Color.END)
                        print(Color.BOLD + Color.ORANGE + '=== ⚖️ Запуск расчета взвешенных долей ===' + Color.END)
                        web_new, _ = results['web']
                        results['weighted_shares'] = self.plmrs_web_pipeline(web_new)
            else:
                print('⏭️ Пропускаем PLMRS пайплайн: требуется выполнить web пайплайн')
        
        # 4. Matched Grids пайплайн (сопоставление сеток VIMB и Palomars)
        if run_matched and self.matched_grid_file and self.hist_vimb_file:
            # ВАЖНО: Для сопоставления сеток нужно явно передать даты через kwargs!
            matched_start_date = kwargs.get('matched_start_date')
            matched_stop_date = kwargs.get('matched_stop_date')

            # Проверяем, что даты для сопоставления переданы
            if matched_start_date is None or matched_stop_date is None:
                print(
                    Color.BOLD + Color.MAROON + \
                    '❌ Ошибка: для Matched Grids пайплайна необходимо указать matched_start_date и matched_stop_date в kwargs!' + \
                    Color.END
                )
                print('💡 Пример: unified_pipeline(run_matched=True, matched_start_date="2024-01-01", matched_stop_date="2024-12-31")')
                print('⏭️ Пропускаем Matched Grids пайплайн')
            else:
                # Определяем источник данных для Palomars grid
                if self.weighted_share_file and os.path.exists(self.weighted_share_file):
                    print(Color.BOLD + Color.DEEP_PINK + '=== 🔗 Запуск пайплайна сопоставления сеток VIMB-Palomars (использую существующий файл) ===' + Color.END)
                    print(Color.BOLD + f'📅 Период сопоставления: {matched_start_date} - {matched_stop_date}' + Color.END)

                    try:
                        results['matched_grids'] = self.matched_grids_pipeline(matched_start_date, matched_stop_date)
                    except Exception as e:
                        print(Color.BOLD + Color.RED + f'❌ Ошибка в Matched Grids пайплайне: {e}' + Color.END)
                else:
                    print(Color.BOLD + Color.RED + '❌ Ошибка: нет данных для сопоставления сеток (требуется weighted_share_file или результаты PLMRS пайплайна)' + Color.END)
                    print('⏭️ Пропускаем Matched Grids пайплайн')
        
        print(Color.BOLD + f'✅ 🏁 Данные для канала {self.channel} успешно выгружены! Спасибо за Ваше ожидание! 😊' + Color.END)
        print('\n')
        
        return results
    

    def unified_pipeline_new(self, run_all: bool = True, parallel: bool = True, **kwargs):
        """
            Гибкий объединенный пайплайн с поддержкой параллельного выполнения.
            
            Параметры:
            ----------
                run_all: bool
                    Если True, запускает все доступные пайплайны
                parallel: bool
                    Если True, выполняет независимые пайплайны параллельно
                **kwargs: 
                    Можно передать какие пайплайны запускать
        """
        results = {}
        
        # Определяем, какие пайплайны запускать
        run_auedience = kwargs.get('run_auedience', run_all or bool(self.auedience_file))
        run_web = kwargs.get('run_web', run_all or bool(self.web_file))
        run_plmrs = kwargs.get('run_plmrs', run_all or bool(self.weighted_share_file))
        run_matched = kwargs.get('run_matched', run_all or (self.matched_grid_file and self.hist_vimb_file))
        
        print(Color.BOLD + f'🚀 Начинаю расчет для канала {self.channel}' + Color.END)
        
        if parallel:
            # Параллельное выполнение независимых пайплайнов
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {}
                
                # Audience и Web независимы, выполняем параллельно
                if run_auedience and self.auedience_file:
                    print(Color.BOLD + Color.VIOLET + '=== 🎬 Запуск выгрузки Total Channels Auedience пайплайна ===' + Color.END)
                    futures[executor.submit(self.auedience_pipeline)] = 'auedience'
                
                if run_web and self.web_file:
                    print(Color.BOLD + Color.BLUE + '=== 🌐 Запуск выгрузки сетки Mediascope пайплайна ===' + Color.END)
                    futures[executor.submit(self.mediascope_web_pipeline)] = 'web'
                
                # Собираем результаты
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        results[key] = future.result()
                    except Exception as e:
                        print(Color.BOLD + Color.RED + f'❌ Ошибка в {key}: {e}' + Color.END)
            
            # PLMRS пайплайн (зависит от Audience и Web)
            if run_plmrs and self.weighted_share_file:
                # Проверяем наличие результатов web
                if 'web' in results and results['web'] is not None:
                    web_new = results['web'][0]  # mediascope_web_pipeline возвращает (web_new, self.web_df)
                    
                    # Проверяем наличие audience
                    if 'auedience' in results and results['auedience'] is not None:
                        print(Color.BOLD + Color.ORANGE + '=== ⚖️ Запуск расчета взвешенных долей ===' + Color.END)
                        results['weighted_shares'] = self.plmrs_web_pipeline(web_new)
                    
                    elif self.auedience_file and os.path.exists(self.auedience_file):
                        # Загружаем из файла, если audience не был вычислен
                        self.total_tv_auedience = pd.read_excel(self.auedience_file)
                        print(Color.BOLD + Color.GREEN + f'Файл c Total TV Auedience для канала {self.channel} найден!' + Color.END)
                        print(Color.BOLD + Color.ORANGE + '=== ⚖️ Запуск расчета взвешенных долей ===' + Color.END)
                        results['weighted_shares'] = self.plmrs_web_pipeline(web_new)
                    else:
                        print(Color.BOLD + Color.RED + '❌ Нет данных аудитории для PLMRS пайплайна' + Color.END)
                else:
                    print(Color.BOLD + Color.YELLOW + '⏭️ Пропускаем PLMRS пайплайн: требуется выполнить web пайплайн' + Color.END)
            
            # Matched Grids пайплайн
            if run_matched and self.matched_grid_file and self.hist_vimb_file:
                matched_start_date = kwargs.get('matched_start_date')
                matched_stop_date = kwargs.get('matched_stop_date')
                
                if matched_start_date is None or matched_stop_date is None:
                    print(Color.BOLD + Color.MAROON + 
                        '❌ Ошибка: для Matched Grids пайплайна необходимо указать matched_start_date и matched_stop_date в kwargs!' + 
                        Color.END)
                    print('💡 Пример: unified_pipeline(run_matched=True, matched_start_date="2024-01-01", matched_stop_date="2024-12-31")')
                elif self.weighted_share_file and os.path.exists(self.weighted_share_file):
                    print(Color.BOLD + Color.DEEP_PINK + '=== 🔗 Запуск пайплайна сопоставления сеток VIMB-Palomars ===' + Color.END)
                    try:
                        results['matched_grids'] = self.matched_grids_pipeline(matched_start_date, matched_stop_date)
                    except Exception as e:
                        print(Color.BOLD + Color.RED + f'❌ Ошибка в Matched Grids: {e}' + Color.END)
                else:
                    print(Color.BOLD + Color.RED + '❌ Нет данных для сопоставления сеток' + Color.END)
        
        else:
            # Синхронное выполнение
            # 1. Audience пайплайн
            if run_auedience and self.auedience_file:
                print(Color.BOLD + Color.VIOLET + '=== 🎬 Запуск выгрузки Total Channels Auedience пайплайна ===' + Color.END)
                results['auedience'] = self.auedience_pipeline()
            
            # 2. Web пайплайн
            if run_web and self.web_file:
                print(Color.BOLD + Color.BLUE + '=== 🌐 Запуск выгрузки сетки Mediascope пайплайна ===' + Color.END)
                results['web'] = self.mediascope_web_pipeline()
            
            # 3. PLMRS пайплайн
            if run_plmrs and self.weighted_share_file:
                if 'web' in results and results['web'] is not None:
                    web_new = results['web'][0]
                    if 'auedience' in results and results['auedience'] is not None:
                        print(Color.BOLD + Color.ORANGE + '=== ⚖️ Запуск расчета взвешенных долей ===' + Color.END)
                        results['weighted_shares'] = self.plmrs_web_pipeline(web_new)
                    elif self.auedience_file and os.path.exists(self.auedience_file):
                        self.total_tv_auedience = pd.read_excel(self.auedience_file)
                        results['weighted_shares'] = self.plmrs_web_pipeline(web_new)
            
            # 4. Matched Grids пайплайн
            if run_matched and self.matched_grid_file and self.hist_vimb_file:
                matched_start_date = kwargs.get('matched_start_date')
                matched_stop_date = kwargs.get('matched_stop_date')
                if matched_start_date and matched_stop_date and self.weighted_share_file:
                    results['matched_grids'] = self.matched_grids_pipeline(matched_start_date, matched_stop_date)
        
        print(Color.BOLD + f'✅ 🏁 Данные для канала {self.channel} успешно выгружены! 😊' + Color.END)
        print('\n')
        
        return results



class ChannelForecasterMaster:
    """
        Класс, в котором реализованы пайплайн для прогнозирования.

        !!! В А Ж Н О !!!
        Класс работает только для конкретного канала!
    """
    def __init__(
            self, 
            channel: str, 
            num_month: int, 
            year: int,
            n_weeks_ago: int,
            n_days_in_fact: int,
            historical_palomars_df: pd.DataFrame,
            historical_vimb_df: pd.DataFrame,
            vocabulary: pd.DataFrame,
            cities: dict,
            holidays_file: str,
            share_fact_df: str,
            total_tv_auedience: pd.DataFrame,
            weighted_share_file: str

        ):
        """
            Атрибуты класса.

            Параметры:
            ----------
            channel: str
                Название канала, для которого будем строить прогноз.
            num_month: int
                Номер месяца, который будем прогнозировать.
            year: int
                Номер года, в котором будем строить прогноз.
            n_weeks_ago: int
                Количество недель из истории, которое будет браться для построения прогноза.
            n_days_in_fact: int
                Количество дней в факте. 
                Если 0, то прогноз строится на весь месяц целиком. 
                В противном случае на часть месяца. Остальные значения фактические!
            historical_palomars_df: pd.DataFrame
                Путь к файлу со смэтченными сетками Palomars-VIMB
            historical_vimb_df: pd.DataFrame
                Путь к файлу с историческими сетками VIMB
            vocabulary: pd.DataFrame
                Таблица-справочник с некоторыми названиями программ
            cities: dict
                Словарь с городами разных стран
            holidays_file: str
                Путь к файлу с праздниками и рабочими субботами для РФ
            total_tv_auedience: pd.DataFrame
                Таблица с фактическими значениями долей в разбивке по дням
            audience_file: str
                Путь к файлу с фактическими значениями весов слотов

        """
        self.channel = channel
        self.num_month = num_month
        self.year = year
        self.n_weeks_ago = n_weeks_ago
        self.n_days_in_fact = n_days_in_fact
        self.historical_palomars_df = historical_palomars_df
        self.historical_vimb_df = historical_vimb_df
        self.vocabulary = vocabulary
        self.cities = cities
        self.holidays_file = holidays_file
        self.share_fact_df = share_fact_df
        self.total_tv_auedience = total_tv_auedience
        self.weighted_share_file = weighted_share_file

        self.MONTHS = {
            1: 'январь', 2: 'февраль', 3: 'март',
            4: 'апрель', 5: 'май', 6: 'июнь',
            7: 'июль', 8: 'август', 9: 'сентябрь',
            10: 'октябрь', 11: 'ноябрь', 12: 'декабрь'
        }

        self.CHANNEL_TARGET_BCA = {
            'ТНТ4': 'All 14-44', 
            '2X2': 'All 11-34', 
            'СТСЛав': 'All 11-34', 
            'Солнце': 'All 10-45', 
            'Карусель': 'All 4-45', 
            'МатчТВ': 'M 14-59', 
            'Суббота': 'W 18-45', 
            'Че': 'All 25-49', 
            'МузТВ': 'All 18-44', 
            'Мир': 'All 25-59', 
            'Спас': 'All 18+', 
            'ТВЦ': 'All 18+', 
            'Звезда': 'All 18+', 
            'Ю': 'W 14-44'
        }

        # Генерация праздников на основе json файла
        #self.work_saturdays, self.all_holidays = RuleBasedForecaster.build_russian_holidays(self.holidays_file)


    def make_params_per_forecast(self):
        """
            Метод по генерации параметров для построения прогноза
        """
        historical_data_copy = self.historical_palomars_df.copy()
        vimb_grid_copy = self.historical_vimb_df.copy()

        # 1. Генерируем параметры для построения прогноза
        self.params = RuleBasedForecaster.generate_forecast_period(
            self.CHANNEL_TARGET_BCA, self.MONTHS, self.num_month, 
            self.channel, self.year, self.n_days_in_fact
        )

        # ПОДГОТОВКА ДАННЫХ Palomars
        fact_part_of_month = pd.DataFrame()
        fact_df_by_programs = pd.DataFrame()
        train = pd.DataFrame()
        vimb_init = pd.DataFrame()

        if self.params['last_fact_date']:
            print(Color.BOLD + Color.GREEN + '🤩 Есть накопленный факт!' + Color.END)

            # Выделяем фактические значения долей из файла с фактическими данными
            mask_fact = (self.share_fact_df['Дата'] >= self.params['start_month']) & (self.share_fact_df['Дата'] <= self.params['last_fact_date'])
            fact_part_of_month = self.share_fact_df[mask_fact].reset_index(drop = True)
            #fact_part_of_month.rename(columns = {f'{self.channel}': 'Share'}, inplace = True)
        
            # Отделяем тренировочную выборку, которую будем использовать для прогнозирования
            train = historical_data_copy[historical_data_copy['Дата'] <= self.params['last_fact_date']].reset_index(drop = True)
            train.rename(columns = {'Share_weighted': 'Share', 'Название программы': 'program_name'}, inplace = True)

            # Формируем датафрейм с фактическими данными в разбивке по программам
            fact_df_by_programs = train[['Дата', 'Название программы init', 'Время выхода', 'Время окончания', 'Share']]
            fact_df_by_programs.rename(columns = {'Название программы init': 'Название программы'}, inplace = True)
            fact_df_by_programs = fact_df_by_programs[
                                            (fact_df_by_programs['Дата'] >= self.params['start_month']) & \
                                            (fact_df_by_programs['Дата'] <= self.params['last_fact_date'])
                                            ].reset_index(drop = True)

            # Выделяем даты в VIMB, которые будем прогнозировать
            mask_part_month = (vimb_grid_copy['Дата'] > self.params['last_fact_date']) & (vimb_grid_copy['Дата'] <= self.params['stop_month'])
            vimb_init = vimb_grid_copy[mask_part_month].reset_index(drop = True)
        
        else:
            print(Color.GREEN + Color.DARK_GRAY + '🙁 Накопленного факта нет. Буду строить прогноз на весь месяц целиком.' + Color.END)
            # Отделяем тренировочную выборку, которую будем использовать для прогнозирования
            train = historical_data_copy[historical_data_copy['Дата'] < self.params['start_month']].reset_index(drop = True)
            train.rename(columns = {'Share_weighted': 'Share', 'Название программы': 'program_name'}, inplace = True)

            # Выделяем даты в VIMB, которые будем прогнозировать
            mask_full_month = (vimb_grid_copy['Дата'] >= self.params['start_date_forecast']) & (vimb_grid_copy['Дата'] <= self.params['stop_month'])
            vimb_init = vimb_grid_copy[mask_full_month].reset_index(drop = True)
        

        self.input_params = {
            'train_df': train,
            'fact_df': fact_part_of_month,
            'fact_by_programs': fact_df_by_programs,
            'vimb_df': vimb_init
        }
        
        return self.input_params
    

    def fit_predict(self):
        """
            Метод для прогнозирования.
        """
        # Финальная подготовка данных
        data_prepr = DataPreparator(self.channel, self.input_params['train_df'], self.input_params['vimb_df'], self.vocabulary)
        all_programs_to_forecast = data_prepr.prepare(self.year, self.num_month, self.cities)

        model = RuleBasedForecaster(
            self.channel, self.year, self.num_month, 
            self.params['start_date_forecast'], self.input_params['train_df'],
            self.holidays_file
        )

        # Итоговая таблица с прогнозом
        forecast_df = model.pipeline_forecaster(
            all_programs_to_forecast, self.input_params['fact_df'], self.n_weeks_ago
        )
        return forecast_df
    

    @staticmethod
    def time_to_minutes(time_str):
        """
            Преобразует строку времени HH:MM:SS в минуты
        """
        h, m, s = map(int, time_str.split(':'))
        return h * 60 + m + (s / 60)  # секунды переводим в доли минут



    def pipeline_predictor(self):
        """
            Полный пайплайн для прогнозирования.
        """
        print(Color.BOLD + Color.ROYAL_BLUE + f'=== 🧘 Начинаю построение прогноза для канала {self.channel} ===' + Color.END)
        print(f'Количество дней в факте {self.n_days_in_fact}. Буду строить прогноз, опираясь на данные за последние {self.n_weeks_ago} недели.')
        print(Color.INDIGO + '🧘 Генерирую входные параметры для прогнозирования и строю прогноз Пожалуйста, подождите ...')

        # 1. Генерация входных параметров
        self.input_params = self.make_params_per_forecast()
        
        # 2. Построение прогноза
        forecast_df = self.fit_predict()

        full_forecast = pd.DataFrame()

        # Если есть накопленный факт, то мы соединяем между собой две таблицы
        if len(self.input_params['fact_by_programs']) != 0:
            df_fact = self.input_params['fact_by_programs']

            # Для фактической части делаем пересчет в "чистую долю" из взвешенной
            df_fact['Канал'] = self.channel
            df_fact['Продолжительность'] = 0.0
            df_fact['Жанр'] = ''
            df_fact['День недели'] = ''

            res = []
            for date in df_fact['Дата'].unique():
                t = df_fact[df_fact['Дата'] == date]

                t['sort_key'] = t['Время выхода'].apply(BaseParser.get_sort_key)

                final = t.sort_values('sort_key').reset_index(drop = True)

                final = final.drop('sort_key', axis = 1)
                res.append(final)
            
            df_fact_result = pd.concat(res).reset_index(drop = True)

            # Пересчет взвешенной доли в "чистую"
            parser = TVPreprocessing(self.channel, self.weighted_share_file, df_fact_result)

            # Расчет "чистой" доли
            new_df_fact, shares = parser.process_daily_weighted_shares(self.total_tv_auedience, reverse = True)

            new_df_fact_cleaned = new_df_fact.drop(['Продолжительность', 'Жанр', 'День недели'], axis = 1)

            forecast_df.drop(['program_type'], axis = 1, inplace = True)
            forecast_df['Share_original'] = 0.0
            forecast_df['Канал'] = self.channel

            full_forecast = pd.concat([new_df_fact_cleaned, forecast_df]).reset_index(drop = True)

        else:
            full_forecast = forecast_df
            full_forecast['Канал'] = self.channel
            full_forecast['Share_original'] = 0.0
        

        # 3. Расчет веса каждой программы в каждом дне
        df = Assistant().calculate_program_duration(full_forecast)
        df.drop(['Время выхода_dt', 'Время окончания_dt'], axis = 1, errors = 'ignore', inplace = True)
        df = df[['Канал', 'Дата', 'Название программы', 'Время выхода', 'Время окончания', 'Продолжительность', 'Share', 'Share_original']]

        # Применяем функцию к столбцу 'Продолжительность'
        df['Продолжительность, мин'] = df['Продолжительность'].apply(ChannelForecasterMaster.time_to_minutes)

        df = df[['Канал', 'Дата', 'Название программы', 'Время выхода', 'Время окончания', 'Продолжительность, мин', 'Share', 'Share_original']]

        df['Вес программы в дне'] = None

        for date in df['Дата'].unique():
            mask = df['Дата'] == date
            sum_dur = df.loc[mask, 'Продолжительность, мин'].sum()  # Более эффективно
            df.loc[mask, 'Вес программы в дне'] = df.loc[mask, 'Продолжительность, мин'] / sum_dur

        final = df[
            [
                'Канал', 'Дата', 'Название программы', 'Время выхода', 
                'Время окончания', 'Продолжительность, мин', 
                'Вес программы в дне', 'Share', 'Share_original'
                ]
        ]
        
        print(Color.BOLD + Color.CRIMSON + '⭐ Прогноз завершён!' + Color.END + '\n')

        return final
    


    #def pipeline_predictor(self):
    #    """
    #        Полный пайплайн для прогнозирования.
    #    """
    #    print(Color.BOLD + Color.ROYAL_BLUE + f'=== 🧘 Начинаю построение прогноза для канала {self.channel} ===' + Color.END)
    #    print(f'Количество дней в факте {self.n_days_in_fact}. Буду строить прогноз, опираясь на данные за последние {self.n_weeks_ago} недели.')
    #    print(Color.INDIGO + '🧘 Генерирую входные параметры для прогнозирования и строю прогноз Пожалуйста, подождите ...')
#
    #    # 1. Генерация входных параметров
    #    self.input_params = self.make_params_per_forecast()
    #    
    #    # 2. Построение прогноза
    #    forecast_df = self.fit_predict()
#
    #    data_forecast = forecast_df.groupby('Дата', as_index = False)['Share'].sum()
    #    data_forecast.rename(columns = {'Share': f'{self.channel}'}, inplace = True)
#
    #    forecast_df = pd.DataFrame()
#
    #    # Если есть накопленный факт, то мы соединяем между собой две таблицы
    #    if len(self.input_params['fact_df']) != 0:
    #        df_fact = self.input_params['fact_df'][['Дата', f'{self.channel}']]        
#
    #        forecast_df = pd.concat([df_fact, data_forecast]).reset_index(drop = True)
#
    #        #print(forecast_df.to_string())
#
    #    else:
    #        forecast_df = data_forecast
#
    #    print(Color.BOLD + Color.CRIMSON + '⭐ Прогноз завершён!' + Color.END + '\n')
#
    #    return forecast_df