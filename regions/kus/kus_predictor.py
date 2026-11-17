import pandas as pd
from pandas.tseries.offsets import DateOffset
import os
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

from OMA_tools.io_data.operations import File, Table, Dict_Operations
from OMA_tools.io_data.dates import Dates_Operations
from OMA_tools.io_data.colors import *

from OMA_tools.regions.data_extraction.export_data import *


MONTHS_RU = {
    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь',
}


BCA_NEED_REPLACE = {
        'All 18+': 'ВСЕ 18+',
        'All 14-59': 'ВСЕ 14-59',
        'All 14-44': 'ВСЕ 14-44',
        'All 25-54': 'ВСЕ 25-54',
        'All 6-54': 'ВСЕ 6-54',
        'All 10-45': 'ВСЕ 10-45',
        'All 14-54': 'ВСЕ 14-54',
        'All 4-45': 'ВСЕ 4-45',
        'All 25-49': 'ВСЕ 25-49',
        'W 25-59': 'Ж 25-59',
        'W 14-44': 'Ж 14-44'
}

REPLACEMENTS = {
            'телеканал': 'канал',
            'санкт-петербург санкт-петербург': 'телеканал санкт-петербург',
            'первый канал': 'первый',
            'пятый канал': '5 канал',
            'тв-3': 'тв3'
}



class KUSCalculation:
    """
        Класс для выгрузки данных по Эфирному рейтингу для дальнейшего прогнозирования КУСа.
    """
    def __init__(self, date_filter):
        self.date_filter = date_filter

        target_date = pd.to_datetime(self.date_filter[0][0])
        self.target_date_str = f"{MONTHS_RU[target_date.month]} {target_date.year}"
    

    @staticmethod
    def format_time(time_int):
        """
            Вспомогательный метод для преобразования временного слота
        """
        if time_int[:2] in ['24', '25', '26', '27', '28', '29']:
            time_int = str(int(time_int[:2]) - 24) + '0000'

        time_str = str(time_int).zfill(6)   

        return f'{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}'


    def calculation(
            self, 
            tasks_json_full: dict, 
            columns_order_path: str,
            by_slots: bool = False
        ):
        """
            Метод для выгрузки данных из API.

            Параметры:
            ----------
                tasks_json_full: dict:
                    Словарь из сформированных задач в формате JSON.
                columns_order_path: str
                    Путь к файлу с нужным порядком колонок.
                by_slots: bool
                    Если True, происходит выгрузка по слотам. Нужна для закрытия месяца (!!!).
                    В противном случае делается обычная выгрузка рейтингов.
            
            Returns:
            ----------
                pd.DataFrame
                    Отсортированная итоговая таблица.
        """
        # ШАГ 1. Загрузка порядка колонок
        columns_order = Dict_Operations.load_pkl_file(columns_order_path)

        columns_table = pd.concat([
            pd.DataFrame({'tvCompanyName': cols[1:], 'ЦА': bca})
            for bca, cols in columns_order.items()
        ]).reset_index(drop = True)

        # ШАГ 2. Выгрузка данных через API
        import time
        start = time.perf_counter()

        results = {}
        exp_serv = ExportService(self.date_filter)

        export_kwargs = dict(
            export_func = exp_serv.export_period,
            statistics = ['RtgPer'],
        )
        if by_slots:
            export_kwargs['slices'] = ['tvCompanyName', 'regionName', 'timeBand60']

        export = partial(exp_serv.export_one, **export_kwargs)

        with ThreadPoolExecutor(max_workers = 8) as executor:
            futures = {
                executor.submit(export, bca, task_json): bca
                for bca, task_json in tasks_json_full.items()
            }
            for future in as_completed(futures):
                bca, df = future.result()
                results[bca] = df
        
        end = time.perf_counter()
        print('\n')
        print(Color.BOLD + Color.GREEN + '=== ДАННЫЕ ВЫГРУЖЕНЫ. ДЕЛАЮ ПРЕОБРАЗОВАНИЯ. ===' + Color.END)
        print(f'Время расчета задач по факту вышло: {((end - start) / 60.):0.2f} мин.')

        # ШАГ 4. Приведение слотов (только если by_slots)
        if by_slots:
            for bca, df in results.items():
                df['timeBand60'] = df['timeBand60'].apply(KUSCalculation.format_time)
        

        # ШАГ 5. МАНИПУЛЯЦИИ С "РОССИЯ 1" И КАНАЛОВ "ГТРК" ДЛЯ АУДИТОРИИ ВСЕ 18+
        all_18 = results['All 18+'].copy()
        all_14_44 = results['All 14-44'].copy()

        merge_kwargs = dict(
            all_18_df=all_18,
            all_14_44_df=all_14_44,
            statistic='RtgPer',
        )
        if by_slots:
            merge_kwargs['columns_to_merge'] = ['regionName', 'ЦА', 'timeBand60']
            merge_kwargs['include_time_band'] = True
        else:
            merge_kwargs['columns_to_merge'] = ['regionName', 'ЦА']
            merge_kwargs['include_time_band'] = False

        all_18_new, all_14_44_new = exp_serv.sum_2_channels(**merge_kwargs)
        results['All 18+'] = all_18_new
        results['All 14-44'] = all_14_44_new

        # ШАГ 6. Итоговый датафрейм
        result_df = pd.concat(results.values()).reset_index(drop = True)
        results_new = pd.merge(
            columns_table, result_df,
            on=['tvCompanyName', 'ЦА'], how = 'inner'
        ).reset_index(drop = True)
        results_new['Month'] = self.target_date_str

        rename_map = {
            'tvCompanyName': 'Channel',
            'RtgPer': 'TVR',
            'ЦА': 'BCA',
        }
        if by_slots:
            rename_map['timeBand60'] = 'time'

        results_new.rename(columns = rename_map, inplace = True)

        output_cols = ['Month', 'TVR', 'Channel', 'BCA']
        if by_slots:
            output_cols = ['Month', 'time', 'TVR', 'Channel', 'BCA']

        results_new = results_new[output_cols]

        # Замена значений на русские в столбце BCA
        results_new['BCA'].replace(BCA_NEED_REPLACE, inplace = True)
        # Убираем скобки из названий каналов-городов
        results_new['Channel'] = results_new['Channel'].str.replace(r'[()]', '', regex = True)

        results_new[['Channel', 'BCA']] = (
            results_new[['Channel', 'BCA']].apply(lambda s: s.str.lower())
        )

        sorted_df = results_new.sort_values(['Channel', 'BCA']).reset_index(drop = True)

        for old, new in REPLACEMENTS.items():
            sorted_df['Channel'] = sorted_df['Channel'].str.replace(old, new, regex = False)

        return sorted_df


    def make_output_table(
            self,
            tasks_json_fact_month: dict,
            columns_order_path: str,
            output_path: str,
            jasks_json_by_slots: dict = None,
            close_month: bool = False
        ):
        """
            Метод для формирования выходной таблицы с выгрузкой в зависимотси от того, какое сегодня обновление: закрытие месяца или обычное.

            Параметры:
            ----------
                tasks_json_fact_month: dict
                    Задачи в формате JSON для расчета фактической части месяца (применительно для обновления в середине месяца).
                columns_order_path: str
                    Путь к файлу с нужным порядком колонок.
                output_path: str
                    Путь к папке, куда сохраняются исторические данные.
                jasks_json_by_slots: dict
                    Задачи в формате JSON для расчета рейтингов по слотам (применительно для закрытия месяца).
                close_month: bool
                    Если True, то месяц закрывается. В противном случае производится обычное обновление в середине месяца. 
                    По умолчанию False.

            Returns:
            ----------
                api_results: list
        """
        api_result_df = []

        # Закрытие месяца
        if close_month == True:

            print(Color.BOLD + Color.CRIMSON + '=== 🚀 СЕГОДНЯ ПРОИЗВОЖУ ЗАКРЫТИЕ ПРОШЛОГО МЕСЯЦА. ===' + Color.END)
            print(
                Color.BOLD + Color.CRIMSON + \
                'ДЕЛАЮ ВЫГРУЗКУ ВСЕГО ПРОШЛОГО МЕСЯЦА, А ТАКЖЕ ВЫГРУЗКУ ВСЕГО ПРОШЛОГО МЕСЯЦА В РАЗБИВКЕ ПО СЛОТАМ.' + \
                Color.END + '\n'
            )
            print(Color.NAVY + 'ВЫГРУЖАЮ ПРЕДЫДУЩИЙ МЕСЯЦ. ПОЖАЛУЙСТА, ПОДОЖДИТЕ ...' + Color.END + '\n')

            fact_month = self.calculation(tasks_json_fact_month, columns_order_path)
            print('✅ Данные за фактическую часть месяца выгружены. Спасибо за ваше ожидание! 😊' + '\n')

            print(Color.NAVY + 'ВЫГРУЖАЮ ПРЕДЫДУЩИЙ МЕСЯЦ С РАЗБИВКОЙ ПО СЛОТАМ. ПОЖАЛУЙСТА, ПОДОЖДИТЕ ...' + Color.END + '\n')
            full_month_by_slots = self.calculation(jasks_json_by_slots, columns_order_path, by_slots = True)
            print('✅ Данные за предыдущий месяц выгружены. Спасибо за ваше ожидание! 😊' + '\n')

            # Объединяем две выгрузки в одну, причем для 
            full_results = full_month_by_slots.merge(
                fact_month, 
                how  = 'left', 
                on = ['Month', 'Channel', 'BCA'], 
                suffixes = ('', ' Total')
            )

            # Загружаем в эксель историрование по месяцам
            column_configs = [
                {'header': 'Month',     'width': 14.0, 'format': 'text'},
                {'header': 'time',      'width': 10.0, 'format': 'time'},
                {'header': 'TVR',       'width': 12.0, 'format': 'number'},
                {'header': 'Channel',   'width': 28.0, 'format': 'text'},
                {'header': 'BCA',       'width': 14.0, 'format': 'text'},
                {'header': 'TVR Total', 'width': 12.0, 'format': 'number'},
            ]

            filename = output_path + self.target_date_str + '.xlsx'

            try:
                with pd.ExcelWriter(filename, engine = 'xlsxwriter') as writer:
                    Builder().make_style_of_table(
                        writer = writer,
                        df = full_results,
                        sheet_name = 'Sheet1',
                        column_configs = column_configs,
                    )
            except Exception as e:
                print(f"❌ Не удалось сохранить файл {filename}: {e}")
                raise
            else:
                print(f"✅ Файл сохранён: {filename}")

            #api_results = [fact_month, full_results]
            return fact_month
        
        # Обновление в середине месяца
        else:

            print(
                Color.BOLD + Color.ORANGE + \
                '=== 🚀 СЕГОДНЯ ОБНОВЛЕНИЕ В СЕРЕДИНЕ МЕСЯЦА. ДЕЛАЮ ВЫГРУЗКУ ФАКТИЧЕСКОЙ ЧАСТИ МЕСЯЦА. ===' + \
                Color.END + '\n'
            )

            fact_month = self.calculation(tasks_json_fact_month, columns_order_path)
            print('✅ Данные за фактическую часть месяца выгружены. Спасибо за ваше ожидание! 😊' + '\n')
            #api_results = [fact_month, None]
            return fact_month
        
        #return api_result


class KUSForecast:
    def __init__(self, ratings_table: pd.DataFrame | None = None):
        """
            Конструктор класса.

            Параметры:
            ----------
                ratings_table: pd.DataFrame (можно не передавать в конструктор)
                    Таблица с эфирными и рекламными рейтингами по закрытым месяцам для всех каналов - городов.
        """
        self.ratings_table = ratings_table
    
    
    @staticmethod
    def sdate(row):
        return datetime.strptime(row,'%B %Y')
    
    
    @staticmethod
    def div(numer, denom):
        return lambda row: 0 if row[denom] == 0 else row[numer] / row[denom]
    
    
    def read_excel_from_start_marker(self, file_path: str, start_marker: str = 'Начало периода') -> pd.DataFrame:
        """
            Метод для чтения файла с определенной строки. Файл начинает считываться только тогда, когда найдена определенная строка.

            Параметры:
            ----------
                file_path: str
                    Путь к файлу, из которого необходимо извлечь данные.
                start_marker: str = 'Начало периода'
                    Строка-маркер, начиная с которого будем считывать файл. По умолчанию 'Начало периода'.
            
            Returns:
            ----------
                start_row - 1: int
                    Метод возвращает количество строк, которое нужно пропустить в файле для успешного его чтения.
        """
        # Читаем весь файл для поиска маркера
        df_temp = pd.read_excel(file_path, header = None)

        # Ищем строку с маркером
        start_row = None
        for idx, row in df_temp.iterrows():
            if any(str(cell).strip() == start_marker for cell in row if pd.notna(cell)):
                start_row = idx + 1  # +1 потому что header=None, данные начинаются со следующей строки
                break

        if start_row is None:
            raise ValueError(f"Маркер '{start_marker}' не найден в файле")
        
        return start_row - 1
    
    
    def from_file(self, path: str, skip: int = 0, col_list: list = [], is_vimb: bool = True) -> pd.DataFrame:
        """
            Метод для чтения данных из указанной папки.

            Параметры:
            ----------
                path: str
                    Путь к папке, в которой хранятся файлы, которые необходимо считать.
                skip: int = 0
                    Количество строк, которые нужно скипнуть в файлах.
                col_list: list
                    Список номеров колонок, которые нужны для анализа.
                is_vimb: bool
                    Булевая переменная.
                    Если True => считываются файлы с выгрузкой из VIMB с рекламными рейтингами.
                    Если False => считываются файлы с выгрузками из БД Mediascope с эфирными рейтингами.
            
            Returns:
            ----------
                result: pd.DataFrame
                    Таблица с результатом
        """
        files = []
        files += os.listdir(path)

        result = pd.DataFrame()
        df = pd.DataFrame()

        for months in files:

            # Если считываем файлы с выгрузками из VIMB
            if is_vimb:
                num_skiprows = self.read_excel_from_start_marker(path + months)
                df = pd.read_excel(path + months, skiprows = num_skiprows, usecols = col_list)

            else:
                df = pd.read_excel(path + months, skiprows = skip, usecols = col_list)

            result = pd.concat([result, df], ignore_index = True)

        return result
    
    
    def parse_VIMB(self, df: pd.DataFrame, is_current_month: bool = True) -> pd.DataFrame:
        """
            Метод для парсинга файлов с выгрузкой из ВИМБа.

            Параметры:
            ----------
                df: pd.DataFrame: 
                    Таблица, с которой нужно произвести преобразования.

                is_current_month: bool = True
                    Булева переменная, характеризующая текущее обновление.
                    Если True => текущий месяц
                    Если False => закрытие месяца
            
            Returns:
            ----------
                df: pd.DataFrame
                    Преобразованная таблица
        """
        # Переименование колонок.
        df.rename(columns = {
            'Время эфира с' : 'time',
            'Начало периода':'Month',
            'Открытый объем без учёта квот' : 'Volume',
            'Открытый WGRP без учета квот' : 'GRP',
            'Базовая ЦА' : 'BCA',
            'Ср. рейтинг' : 'tvr_adv',
            'Канал': 'Channel'
        },
        inplace = True)  

        df['BCA'] = df['BCA'].str.split(' ', expand = True)[0].str.lower() + ' ' + df['BCA'].str.split(' ', expand = True)[1]
        df['Channel'] = df['Channel'].str.lower()
    
        if is_current_month is not True:
            df['time'] = df['time'].astype(str)

        if not pd.api.types.is_datetime64_any_dtype(df['Month']):
            df['Month'] = pd.to_datetime(df['Month'])
        
        # Теперь применяем strftime ко всему столбцу
        df['Month'] = df['Month'].dt.strftime('%B %Y')

        return df
    
    
    def fit_predict(
            self, 
            data_api: pd.DataFrame, 
            first_date: str, 
            last_date: str, 
            filepath_vimb_curr_month: str
        ) -> pd.DataFrame:
        """
            Метод для непосредственного прогнозирования КУС.

            Параметры:
            ----------
                data_api: pd.DataFrame
                    Выгрузка с TVR из БД Mediascope ЗА ФАКТИЧЕСКУЮ ЧАСТЬ МЕСЯЦА.
                first_date: str
                    Дата начала вывода данных в формате строки.
                last_date: str
                    Дата окончания вывода данных в формате строки.
                filepath_vimb_curr_month: str 
                    Путь к файлу с выгрузкой из VIMB по текущему месяцу.

            Returns:
            ----------
                final_result: pd.DataFrame
                    Итоговая таблица с прогнозом КУС.
        """
        final_result = pd.DataFrame()
        
        today = date.today()

        # дата начала и окончания исторических данных для предсказания куса (база прогноза)
        first_fact_date = today - pd.offsets.MonthBegin()- DateOffset(months = 6) # 1е число пол года назад
        last_fact_date = today - pd.offsets.MonthBegin()   

        #table = self.table

        ################################### ОБЩАЯ ПОДГОТОВКА #############################################################
        self.ratings_table['Month'] = self.ratings_table['Month'].apply(KUSForecast.sdate)
        self.ratings_table['PredWeight'] = self.ratings_table['TVR'] / self.ratings_table['TVR Total']
        self.ratings_table['PredKM'] = self.ratings_table.apply(KUSForecast.div('tvr_adv', 'TVR'), axis = 1)
        self.ratings_table = self.ratings_table.astype({"PredKM": "float64"})


        ############################# ПОДСЧЕТ ЗАКРЫТЫХ МЕСЯЦЕВ ВСЕ КАНАЛЫ СРАЗУ #####################################################
        closed_periods_df = self.ratings_table[
            (self.ratings_table['Month'] >= first_date) & \
            (self.ratings_table['Month'] < last_fact_date)
        ]

        closed_periods_df = closed_periods_df.pivot_table(
            index = 'Channel', 
            columns = ['Month'],
            values = ['TVR Total', 'Volume', 'GRP'],
            aggfunc = {'TVR Total': 'mean', 'Volume': 'sum','GRP': 'sum'}
        )

        if closed_periods_df.empty != True:
            normalized_grp = closed_periods_df['GRP'] * 20 / (closed_periods_df['Volume'] * closed_periods_df['TVR Total'])
            
        else:
            normalized_grp = pd.DataFrame( index = pd.Index(self.ratings_table['Channel'].unique(), name = 'Channel') )
            print('Нет месяцев в факте!')


        ############################# ПРОГНОЗ МЕСЯЦЕВ ВСЕ КАНАЛЫ СРАЗУ #####################################################

        # Прогнозный датафрейм
        forecast_period = self.ratings_table[
            (self.ratings_table['Month'] >= last_fact_date) & \
            (self.ratings_table['Month'] <= last_date)
        ]

        # Датафрейм с фактическими данными
        fact_period = self.ratings_table[
            (self.ratings_table['Month'] >= first_fact_date) & \
            (self.ratings_table['Month'] < last_fact_date)
        ]

        fact_period_pivoted = fact_period.pivot_table(
            index = ['Channel', 'time'],
            values = ['PredWeight', 'PredKM'],
            aggfunc = {'PredWeight': 'mean', 'PredKM': 'median'}
        )
        
        # Join таблиц с фактом и прогнозом
        forecast_fact_merged = pd.merge(
            forecast_period[['Channel', 'Month', 'time', 'BCA', 'Volume']], 
            fact_period_pivoted, 
            how = "left", 
            on = ['Channel', "time"]
        )
        
        # Заполнение пропусков
        forecast_fact_merged['PredKM'] = forecast_fact_merged.groupby('Channel')['PredKM'].transform(
            lambda x: x.fillna(x.rolling(2, min_periods = 1).mean().ffill().bfill())
        )

        # Таблица с прогнозом
        forecast_df = forecast_fact_merged.pivot_table(
            index = ['Channel'], 
            columns = 'Month', 
            values = ['PredWeight'], 
            aggfunc = lambda x: ( 
                forecast_fact_merged.loc[x.index, 'PredWeight'] * forecast_fact_merged.loc[x.index,'PredKM'] * forecast_fact_merged.loc[x.index, 'Volume'] 
            ).sum() / forecast_fact_merged.loc[x.index, 'Volume'].sum()
        ).droplevel(0, axis = 1)

        ############################# ПОДСЧЕТ ТЕКУЩЕГО МЕСЯЦА #####################################################
        # Если текущий месяц январь
        if today.month == 1:

            if today.day > 14:
                print(Color.BOLD + 'Реализуем обновление в середине месяца' + Color.END)

                if today.day < 21:
                    fact_weight = 0.5        # вес фактического куска
                    forecast_weight = 0.5    # вес прогнозного куска

                else:
                    fact_weight = 0.7        # вес фактического куска
                    forecast_weight = 0.3    # вес прогнозного куска

                # Загрузка данных VIMB и БД Mediascope 
                tvr_df = pd.read_excel(
                    filepath_vimb_curr_month,
                    skiprows = 19,
                    usecols = [0, 5, 6, 7, 12, 13]
                )
                tvr_df = KUSForecast.parse_VIMB(tvr_df)

                # Соединение фактических кусков текущего месяца по рекламному рейтингу tvr из VIMB и эфирному рейтингу TVR из БД Mediascope 
                full_data = pd.merge(data_api, tvr_df, how = "right", on = ['Channel', 'BCA', "Month"])

                # Подсчет факта
                full_data['kus_f'] = full_data['tvr_adv'] / full_data['TVR'] # создание столбца с фактическим расчетным КУС

                # JOIN факта текущего месяца с прогнозом на дальние месяцы
                result_df = pd.merge(forecast_df, full_data[['kus_f','Channel']], how = "left", on = ['Channel'])
                result_df['tec_m_progn'] = result_df.iloc[:,1]

                # Корректировка прогноза на текущий месяц через веса
                result_df.iloc[:, 1] = result_df['kus_f'] * fact_weight + (result_df.iloc[:, 1]) * forecast_weight
                final_result = normalized_grp.merge(result_df,on = 'Channel', how = 'left')

            else:
                print('Закрытие месяца!')
                final_result = normalized_grp.merge(forecast_df, on = 'Channel', how = 'left')

        # Если текущий месяц не январь
        else:
            if today.day > 10:
                print('Реализуем обновление в середине месяца')
                if today.day < 21:
                    fact_weight = 0.5
                    forecast_weight = 0.5
                else:
                    fact_weight = 0.7
                    forecast_weight = 0.3     

                # Загрузка данных VIMB и БД Mediascope 
                tvr_df = pd.read_excel(
                    filepath_vimb_curr_month,
                    skiprows = 19,
                    usecols = [0, 5, 6, 7, 12, 13]
                )
                tvr_df = KUSForecast.parse_VIMB(tvr_df)

                # Соединение фактических кусков текущего месяца по рекламному рейтингу tvr из VIMB и эфирному рейтингу TVR из БД Mediascope 
                full_data = pd.merge(data_api, tvr_df, how = "right", on = ['Channel', 'BCA', "Month"])

                # Подсчет факта
                full_data['kus_f'] = full_data['tvr_adv'] / full_data['TVR']  # создание столбца с фактическим расчетным КУС

                # JOIN факта текущего месяца с прогнозом на дальние месяцы
                result_df = pd.merge(forecast_df, full_data[['kus_f','Channel']], how = "left", on = ['Channel'])
                result_df['tec_m_progn'] = result_df.iloc[:,1]

                # Корректировка прогноза на текущий месяц через веса
                result_df.iloc[:, 1] = result_df['kus_f'] * fact_weight + (result_df.iloc[:, 1]) * forecast_weight
                final_result = normalized_grp.merge(result_df,on = 'Channel', how = 'left')

            else:
                print('Закрытие месяца!')
                final_result = normalized_grp.merge(forecast_df, on = 'Channel', how = 'left')

        return final_result
    

    def make_kus_forecast(
            self, 
            current_df_api: pd.DataFrame, 
            year: int, 
            vimb_tvr_filepath: str,
            TVR_filepath: str,
            vimb_curr_month_file: str,
            output_filepath: str
        ):
        """
            Метод для прогнозирования КУСа 
        """
        output_file = f'{output_filepath}КУС предсказание регионы {year}.xlsx'

        # ШАГ 1. Чтение файлов из ВИМБа с рейкламным рейтингом tvr.
        vimb_tvr = self.from_file(vimb_tvr_filepath, 18, [0, 2, 7, 8, 9, 14, 15])
        vimb_tvr = self.parse_VIMB(vimb_tvr, False)

        # Создаем копию. Далее работаем с ней
        tvr_df_copy = vimb_tvr.copy()

        # ШАГ 2. Чтение файлов с эфирным рейтингом TVR по закрытым месяцам.
        TVR = self.from_file(TVR_filepath, col_list = [1, 2, 3, 4, 5, 6], is_vimb = False)
        # Создаем копию. Далее работаем с ней
        TVR_df_copy = TVR.copy()

        # ШАГ 3. Создание фулл таблицы, используя рекламные рейтинги и эфирные
        self.ratings_table = pd.merge(TVR_df_copy, tvr_df_copy, how = 'right', on = ['Channel', 'BCA', 'Month', 'time'])

        # ШАГ 4. Прогнозирование
        kus_forecast = self.fit_predict(
                            current_df_api, 
                            pd.to_datetime(f'{year}-01-01'),                             
                            pd.to_datetime(f'{year}-12-31'),
                            vimb_curr_month_file
                        )
        
        # ШАГ 5. Сохранение в файл .xlsx прогноз КУС
        kus_forecast.to_excel(output_file)
        return kus_forecast
        

    def kus_pipeline(
            self, 
            current_year: int, 
            today_day: int, 
            today_month: int,
            periods: dict,
            json_tasks_fact_month: dict,
            json_tasks_full_month: dict,
            columns_order_file_path: str,
            historical_path: str,
            vimb_tvr_filepath: str,
            TVR_filepath: str,
            vimb_curr_month_file: str,
            output_filepath: str

        ):
        """
            Метод для выгрузки данных из БД Mediascope по условиям.

            Параметры:
            ----------
                current_year: int
                    Номер года
                today_day: int
                    Номер сегодняшнего дня в текущем месяце.
                today_month: int
                    Номер текущего месяца.
                periods: dict
                    Словарь с периодами для выгрузки данных из БД Mediascope.
                json_tasks_fact_month: dict
                    Словарь с задачами в формате JSON для выгрузки факта текущего месяца.
                json_tasks_full_month: dict
                    Словарь с задачами в формате JSON для выгрузки прошлого месяца.
                columns_order_file_path: str
                    Путь к файлу с прогнозируемыми регионами
                historical_path: str
                    Путь к папке с историческими данными TVR ('эфирный рейтинг')
                vimb_tvr_filepath: str
                    Путь к папке с выгрузками рекламного рейтинга tvr из ВИМБ по закрытым периодам.
                TVR_filepath: str
                    Путь к папке с выгрузками эфирного рейтинга TVR по закрытым месяцам из БД Mediascope.
                vimb_curr_month_file: str
                    Путь к файлу свыгрузкой рекламного рейтинга tvr из ВИМБ по ТЕКУЩЕГО МЕСЯЦУ.
                output_filepath: str
                    Название выходного файла с прогнозом.
        """
        forecast_df = pd.DataFrame()

        from datetime import date
        today = date.today()

        # Если текущий месяц Январь
        if today_month == 1:
            print(Color.BOLD + Color.SEA_GREEN + f"Текущий месяц ЯНВАРЬ {current_year} года." + Color.END)

            # Необходимо закрывать предыдущий год и сделать обновление в середине текущего месяца
            if today_day > 1 and today_day <= 16:
                print(Color.BOLD + "Сегодня предыдущий месяц закрываем! Пожалуйста, подождите ..." + Color.END)
                print(
                    Color.BOLD + \
                    "Делаю выгрузку всего прошлого месяца в разбивке по слотам, а также выгрузку всего прошлого месяца без разбивки" +\
                    Color.END
                )

                kus_calc = KUSCalculation(periods['full_month'])
                result_df = kus_calc.make_output_table(
                    json_tasks_fact_month, 
                    columns_order_file_path,
                    historical_path,
                    json_tasks_full_month,
                    close_month = True
                )

                print(Color.BOLD + Color.ORANGE + 'Строю прогноз. Пожалуйста, подождите ...' + Color.END)
                forecast_df = self.make_kus_forecast(
                    current_df_api = result_df, 
                    year = current_year - 1,
                    vimb_tvr_filepath = vimb_tvr_filepath,
                    TVR_filepath = TVR_filepath,
                    vimb_curr_month_file = vimb_curr_month_file,
                    output_filepath = output_filepath
                )
            
            # Обновление в середине января
            else:
                print(Color.BOLD + "Обновление в середине месяца. Выгружаю фактическую часть Января. Пожалуйста, подождите ..." + Color.END)

                kus_calc = KUSCalculation(periods['fact_month'])
                result_df = kus_calc.make_output_table(
                    json_tasks_fact_month, 
                    columns_order_file_path,
                    historical_path,
                    json_tasks_full_month
                )

                print(Color.BOLD + Color.ORANGE + 'Строю прогноз. Пожалуйста, подождите ...' + Color.END)
                forecast_df = self.make_kus_forecast(
                    current_df_api = result_df, 
                    year = current_year,
                    vimb_tvr_filepath = vimb_tvr_filepath,
                    TVR_filepath = TVR_filepath,
                    vimb_curr_month_file = vimb_curr_month_file,
                    output_filepath = output_filepath
                )
        
        # Текущий месяц не Январь
        else:
            # Закрытие месяца
            if today.day > 1 and today.day <= 10:
                #today_month = f"{MONTHS_RU[today.month - 1]}"
                print(
                    Color.BOLD + Color.SEA_GREEN + \
                    f"Закрываю предыдущий месяц {{MONTHS_RU[today.month - 1]}}. " + \
                    "Делаю выгрузку всего прошлого месяца в разбивке по слотам, а также выгрузку всего прошлого месяца без разбивки. " + \
                    "Пожалуйста, подождите ..." + Color.END
                )

                kus_calc = KUSCalculation(periods['full_month'])
                result_df = kus_calc.make_output_table(
                    json_tasks_fact_month, 
                    columns_order_file_path,
                    historical_path,
                    json_tasks_full_month,
                    close_month = True
                )

                print(Color.BOLD + Color.ORANGE + 'Строю прогноз. Пожалуйста, подождите ...' + Color.END)
                forecast_df = self.make_kus_forecast(
                    current_df_api = result_df, 
                    year = current_year,
                    vimb_tvr_filepath = vimb_tvr_filepath,
                    TVR_filepath = TVR_filepath,
                    vimb_curr_month_file = vimb_curr_month_file,
                    output_filepath = output_filepath
                )
            

            # Обновление в середине месяца
            else:
                today_month = f"{MONTHS_RU[today.month]}"
                print(
                    Color.BOLD + Color.SEA_GREEN + \
                    f"Делаю обновление в середине месяца {MONTHS_RU[today.month]}. Выгружаю фактическую часть текущего месяца. " + \
                    "Пожалуйста, подождите ..." + Color.END
                )

                kus_calc = KUSCalculation(periods['fact_month'])
                result_df = kus_calc.make_output_table(
                    json_tasks_fact_month, 
                    columns_order_file_path,
                    historical_path,
                    json_tasks_full_month
                )

                forecast_df = self.make_kus_forecast(
                    current_df_api = result_df, 
                    year = current_year,
                    vimb_tvr_filepath = vimb_tvr_filepath,
                    TVR_filepath = TVR_filepath,
                    vimb_curr_month_file = vimb_curr_month_file,
                    output_filepath = output_filepath
                )

        return forecast_df
