import numpy as np
import pandas as pd
from typing import Tuple, Optional, List, Dict, Callable
from datetime import timedelta, datetime, time
from dateutil.relativedelta import relativedelta
from difflib import SequenceMatcher
#from OMA_tools.federal.channel_forecast.core.simple_models import *
from OMA_tools.io_data.colors import *
import traceback
traceback.print_exc()


class TVShareCalculator:
    """
        Класс для расчета долей в конкретных слотах через вес слота и процент длительности программы в часе
    """
    def __init__(self, channel, df):
        """
            Атрибуты класса
            Args:
                df: pd.DataFrame: Датафрейм с исходной долей
        """
        self.channel = channel
        self.df = df
        self._validate_data()
    

    def _validate_data(self) -> None:
        """
            Проверка обязательных колонок в данных.
        """
        required_columns = ['Дата', 'Время выхода', 'Время окончания']
        missing = [col for col in required_columns if col not in self.df.columns]
        if missing:
            raise ValueError(f'Отсутствуют обязательные колонки: {missing}')
    

    @staticmethod
    def calculate_slot_weights(
                    total_tv_audience: pd.DataFrame,
                    rating_col: str = 'Auedience',
                    timeslot_col: str = 'TimeSlot',
                    date_col: str = 'Дата'
                ) -> pd.DataFrame:
        """
            Рассчитывает веса слотов на основе данных о TotalTVAudience.
        
            Args:
                total_tv_audience: Датафрейм с аудиторными данными
                rating_col: Название колонки с рейтингом
                timeslot_col: Название колонки с временным слотом
                date_col: Название колонки с датой
                
            Returns:
                Датафрейм с весами слотов
        """
        df = total_tv_audience.copy()
        
        df['TimeSlot_dt'] = pd.to_datetime(df[timeslot_col])
        df['hour_start'] = df['TimeSlot_dt'].dt.hour
        
        # Группировка и расчет весов
        df['daily_total'] = df.groupby(date_col)[rating_col].transform('sum')
        df['Slot_weight'] = df[rating_col] / df['daily_total']
        
        # Замена бесконечно малых значений
        df['Slot_weight'] = df['Slot_weight'].fillna(0)
        
        return df[[date_col, timeslot_col, rating_col, 'Slot_weight', 'hour_start']].reset_index(drop = True)
    

    @staticmethod
    def get_hour(dt, param: str):
        """
            Метод для генерации часа. 
            Возможные опции: начало текущего часа, конец текущего часа, начало следующего часа, начало предыдущего часа
        """
        # Вариант 1: Начало текущего часа
        if param == 'start_of_current_hour':
            return dt.replace(minute = 0, second = 0, microsecond = 0)

        # Вариант 2: Конец текущего часа
        elif param == 'end_of_current_hour':
            return dt.replace(minute = 59, second = 59, microsecond = 0)
        
        # Вариант 3: Старт следующего часа
        elif param == 'start_next_hour':
            return dt.replace(minute = 0, second = 0, microsecond = 0) + timedelta(hours = 1)
        
        # Вариант 4: Старт прошлого часа
        elif param == 'start_previous_hour':
            return dt.replace(minute = 0, second = 0, microsecond = 0) + timedelta(hours = -1)
    

    def round_time(self, time_column: str, minutes = 1, method = 'round'):
        """
            Точное округление времени до минут без использования float.
            
            Args:

                time_series : pd.Series: Серия со временем в формате 'HH:MM:SS'
                minutes : int: Шаг округления в минутах (1, 5, 10, 15, 30, 60)
                method : str: Метод округления: 'round', 'floor', 'ceil'
            
            Returns:
                pd.Series: Округленное время
        """
        df = self.df.copy()
        time_series = df[time_column]

        def round_single_time(time_str, minutes_step, method_type):
            # Разбираем время
            if isinstance(time_str, str):
                h, m, s = map(int, time_str.split(':'))
            elif hasattr(time_str, 'hour'):  # Если это datetime.time
                h, m, s = time_str.hour, time_str.minute, time_str.second
            else:
                return time_str

            # Если имеем начало часа, например, 05:00:00, то возвращаем в исходном виде
            if m == 0.0 and s == 0.0:
                return f'{h:02d}:00:00'

            # Если время кривое
            else:
            
                # Общее количество секунд
                total_seconds = h * 3600 + m * 60 + s
                step_seconds = minutes_step * 60
                
                if method_type == 'floor':
                    # Округление вниз
                    rounded_seconds = (total_seconds // step_seconds) * step_seconds
                elif method_type == 'ceil':
                    # Округление вверх
                    if total_seconds % step_seconds == 0:
                        rounded_seconds = total_seconds
                    else:
                        rounded_seconds = ((total_seconds // step_seconds) + 1) * step_seconds
                else:  # 'round' - стандартное округление
                    # Количество секунд от начала интервала
                    remainder = total_seconds % step_seconds
                    
                    # Если остаток >= половины интервала, округляем вверх
                    if remainder >= step_seconds / 2:
                        rounded_seconds = ((total_seconds // step_seconds) + 1) * step_seconds
                    else:
                        rounded_seconds = (total_seconds // step_seconds) * step_seconds
                
                # Преобразуем обратно
                new_h = (rounded_seconds // 3600) % 24
                new_m = (rounded_seconds % 3600) // 60
                
                return f'{new_h:02d}:{new_m:02d}:00'
        
        # Применяем к каждой строке
        return time_series.apply(lambda x: round_single_time(x, minutes, method))
    

    def adjust_hour_start(self, end_col = 'Время окончания', start_col = 'Время выхода'):
        """
            Заменяет XX:00:00 на XX:59:59, только если это действительно 
            начало нового часа в расписании (т.е. следующее время выхода начинается с этого часа).
        """
        df_adj = self.df.copy()
        
        for i in range(len(df_adj) - 1):
            current_end = df_adj.loc[i, end_col]
            next_start = df_adj.loc[i + 1, start_col]
            
            # Если текущее окончание - начало часа И следующее время выхода начинается с этого же часа
            if current_end.endswith(':00:00') and next_start.startswith(current_end[:2]):
                hours = int(current_end.split(':')[0])
                
                if hours == 0:
                    new_time = '23:59:59'
                else:
                    new_hour = hours - 1
                    new_time = f"{new_hour:02d}:59:59"
                
                df_adj.loc[i, end_col] = new_time
        
        # Обрабатываем последнюю строку отдельно
        if df_adj.loc[df_adj.index[-1], end_col].endswith(':00:00'):
            hours = int(df_adj.loc[df_adj.index[-1], end_col].split(':')[0])
            if hours == 0:
                new_time = '23:59:59'
            else:
                new_hour = hours - 1
                new_time = f'{new_hour:02d}:59:59'
            df_adj.loc[df_adj.index[-1], end_col] = new_time
        
        return df_adj

    

    def calculate_hour_jump(self, start_col = 'Время выхода', end_col = 'Время окончания'):
        """
            Рассчитывает количество скачков через час для всех программ в DataFrame.
            
            Args:
                df: DataFrame с колонками времени
                start_col: название колонки с временем начала
                end_col: название колонки с временем окончания
            
            Returns:
                DataFrame с добавленной колонкой 'Скачки через час'
        """
        result_df = self.df.copy()

        jumps_list = []
        hours_list = []
        durations_list = []
        
        for idx, row in result_df.iterrows():
            try:
                # Парсим время
                start_time = datetime.strptime(str(row[start_col]), '%H:%M:%S')
                end_time = datetime.strptime(str(row[end_col]), '%H:%M:%S')
                
                # Корректируем время окончания при переходе через полночь
                if end_time <= start_time:
                    end_time += timedelta(days = 1)
                
                # Рассчитываем количество скачков
                current_time = start_time
                hour_jumps = 0
                hours_covered = []
                
                while current_time < end_time:
                    current_hour = current_time.hour
                    hours_covered.append(current_hour)
                    
                    # Определяем начало следующего часа
                    next_hour_start = current_time.replace(minute = 0, second = 0, microsecond = 0) + timedelta(hours = 1)
                    
                    # Если следующий час не превышает время окончания, это скачок
                    if next_hour_start < end_time:
                        hour_jumps += 1
                    
                    # Переходим к следующему часу
                    current_time = next_hour_start
                
                # Длительность в минутах
                duration = (end_time - start_time).total_seconds() / 60.0
                
                jumps_list.append(hour_jumps)
                hours_list.append(hours_covered)
                durations_list.append(duration)
                
            except Exception as e:
                print(f'Ошибка в строке {idx}: {e}')
                jumps_list.append(0)
                hours_list.append([])
                durations_list.append(0)
        
        # Добавляем результаты в DataFrame
        result_df['Скачки через час'] = jumps_list
        result_df['Пройдено часов'] = hours_list
        result_df['Длительность (мин)'] = durations_list
        
        return result_df
    

    def calculate_weighted_share(self, auedience, reverse: bool = False) -> pd.DataFrame:
        """
            Функция для расчета взвешенной доли для какого-то конкретного дня.
            Args:
                auedience: pd.DataFrame: 
                    ДатаФрейм с весами слотов, посчитанными через TotalTVAuedience для конкретного дня.
                reverse: bool
                    Переменная типа bool. По умолчанию False. Если True, то необходимо делать обратный пересчет на "нормальную"
                    не взвешенную долю.
            Returns:
                res: pd.DataFrame: Датафрейм с новой рассчитанной долей для какого-то конкретного дня
                share_sum: суммарная доля для какого-то конкретного дня
        """
        # Определяем количество скачков через час в датафрейме
        result_df = self.calculate_hour_jump()

        df = result_df.copy()

        df['Share'] = df['Share'].astype(float)
        # Создаём столбец с новой долей
        df['Share_weighted'] = 0.0

        # Создаем словарь весов для быстрого доступа
        weight_dict = dict(zip(auedience['hour_start'], auedience['Slot_weight']))
        
        for i in range(len(df)):
            num_of_jumps = df.iloc[i]['Пройдено часов']
            program_start = df.iloc[i]['Время выхода']
            program_finish = df.iloc[i]['Время окончания']
            share = df.iloc[i]['Share']

            start_dt = datetime.strptime(program_start, '%H:%M:%S')
            end_dt = datetime.strptime(program_finish, '%H:%M:%S')

            # Обрабатываем переход через полночь
            if end_dt <= start_dt:
                end_dt += timedelta(days = 1)

            coeffs = []

            # Если скачка нет (программа в пределах одного часа)
            if len(num_of_jumps) == 1:
                
                # Определение часа старта для подбора веса слота
                hour = num_of_jumps[0]

                # Длительность в минутах
                duration_minutes = (end_dt - start_dt).total_seconds() / 60.0
                # % длительности программы в часе
                percent_duration = duration_minutes / 60.0

                # Получаем вес слота
                slot_weight = weight_dict.get(hour, 1.0)

                # Если скачка через час нет, считаем долю в слоте как Share * вес слота * % длительности программы в часе
                coeffs.append(percent_duration * slot_weight)
                
            # Если есть скачки (он необязательно должен быть 1)
            else:
                for k in range(len(num_of_jumps)):
                    # Первый скачок
                    if k == 0:
                        # Определяем конец первого часа
                        end_hour = datetime.strptime(f'{num_of_jumps[k]:02d}:59:59', '%H:%M:%S')
                        # Длительность в минутах
                        duration_minutes = (end_hour - start_dt).total_seconds() / 60.0
                        # % длительности программы в часе
                        percent_duration = duration_minutes / 60.0


                        # Получаем вес слота
                        slot_weight = weight_dict.get(num_of_jumps[0], 1.0)

                        coeffs.append(percent_duration * slot_weight)
                    
                    # Последний скачок
                    elif k == len(num_of_jumps) - 1:
                        # Определяем начало последнего часа
                        start_hour = datetime.strptime(f'{num_of_jumps[k]:02d}:00:00', '%H:%M:%S')

                        # Если start_hour меньше start_dt (переход через полночь), добавляем день
                        if start_hour < start_dt:
                            start_hour += timedelta(days = 1)

                        # Длительность в минутах
                        duration_minutes = (end_dt - start_hour).total_seconds() / 60.0
                        # % длительности программы в часе
                        percent_duration = duration_minutes / 60.0

                        # Получаем вес слота
                        slot_weight = weight_dict.get(num_of_jumps[-1], 1.0)

                        coeffs.append(percent_duration * slot_weight)

                    # Промежуточный скачок
                    else:
                        # Определяем начало часа
                        start_hour = datetime.strptime(f'{num_of_jumps[k]:02d}:00:00', '%H:%M:%S')

                        # Определяем конец часа
                        end_hour = datetime.strptime(f'{num_of_jumps[k]:02d}:59:59', '%H:%M:%S')
                        # Длительность в минутах
                        duration_minutes = (end_hour - start_hour).total_seconds() / 60.0
                        # % длительности программы в часе
                        percent_duration = duration_minutes / 60.0

                        # Получаем вес слота
                        slot_weight = weight_dict.get(num_of_jumps[k], 1.0)

                        coeffs.append(percent_duration * slot_weight)
                    
            if len(coeffs) != 0:
                coefficient = np.sum(coeffs)
                
                # если нужно посчитать "чистую" долю
                if reverse:
                    df.at[i, 'Share_weighted'] = share / coefficient

                # если нужно посчитать "взвешенную" долю
                else:
                    df.at[i, 'Share_weighted'] = share * coefficient

        res = df[
            [
                'Канал', 'Дата', 'Название программы', 
                'Время выхода', 'Время окончания',
                'Share', 'Share_weighted', 'Жанр', 'День недели'
                ]
            ]
        
        # Подсчет длительностей программ
        res['Время выхода_dt'] = pd.to_datetime(res['Время выхода'])
        res['Время окончания_dt'] = pd.to_datetime(res['Время окончания'])

        # Автоматически корректируем переход через полночь
        res['Время окончания_dt'] = np.where(
            res['Время окончания_dt'] < res['Время выхода_dt'],
            res['Время окончания_dt'] + pd.Timedelta(days = 1),
            res['Время окончания_dt']
        )

        res['Продолжительность'] = (
            pd.to_datetime(res['Время окончания_dt']) - pd.to_datetime(res['Время выхода_dt'])
        ).dt.total_seconds()

        # Форматирование
        res['Продолжительность'] = res['Продолжительность'].apply(
            lambda x: f"{int(x//3600):02d}:{int((x%3600)//60):02d}:{int(x%60):02d}"
        )

        res = df[
            [
                'Канал', 'Дата', 'Название программы', 
                'Время выхода', 'Время окончания', 'Продолжительность',
                'Share', 'Share_weighted', 'Жанр', 'День недели'
                ]
            ]
        
        res['Share_weighted'] = res['Share_weighted'].astype(float)
        # Расчёт суммарной доли по дню
        share_sum = np.sum(list(res['Share_weighted']))

        return res, share_sum



class TVScheduleProcessor:
    """
        Класс для подгона сетки Palomars под сетку VIMB из Сводного отчёта для одного дня
    """

    def __init__(self, channel, vimb_init, palomars_init):
        """
            Атрибуты:
                vimb_init: pd.DataFrame: исходная сетка ТВ-программ VIMB
                palomars_init: pd.DataFrame: исходная сетка ТВ-программ Mediascope
        """
        self.channel = channel
        self.vimb_init = vimb_init
        self.palomars_init = palomars_init
    

    def convert_data_column(self, df):
        """
            Всопомгательный метод для конвертации столбца с названием 'Дата'
        """
        df['Дата'] = pd.to_datetime(df['Дата'])
        df['Дата'] = df["Дата"].dt.strftime("%Y-%m-%d")
        return df
    

    def adjust_end_time(
                    self, 
                    df: pd.DataFrame, 
                    time_col: str = 'Время окончания'
                ) -> pd.DataFrame:
        """
            Корректировка времени окончания для обработки границ часов. Если время окончания, например, 05:00:00, то будет сделана замена на 04:59:59.
            Отдельно обрабатывается перескок через полночь.

            Args:
                df: датафрейм, в котором хотим произвести конвертацию времени.
                time_col: str: название колонки, в которой хотим сделать конвертацию. По умолчанию 'Время окончания'.

            Returns:
                Датафрейм df с конвертированными слотами Времени окончания программ.
        """

        def adjust_time__(time_str: str) -> str:
            h, m, s = map(int, time_str.split(':'))

            if m == 0 and s == 0:

                # Если полночь
                if h == 0:
                    return '23:59:59'

                return f'{(h - 1):02d}:59:59'

            return time_str

        df = df.copy()
        df[time_col] = df[time_col].apply(adjust_time__)
        return df
    

    def fix_invalid_times(self, df):
        """
            Исправляет времена, если они одинаковые или начало позже конца
        """
        for i in range(len(df)):
            start = str(df.iloc[i]['Время выхода'])
            stop = str(df.iloc[i]['Время окончания'])
            
            base_date = '2000-01-01 '
            start_dt = pd.to_datetime(base_date + start)
            stop_dt = pd.to_datetime(base_date + stop)
            
            if start == stop or start_dt > stop_dt:
                df.at[i, 'Время выхода'] = df.iloc[i]['Время выхода init']
                df.at[i, 'Время окончания'] = df.iloc[i]['Время окончания init']
        return df
    


    def join_broadcasts(self, data, type: str, include_share: bool = True):
        """
            Метод реализует объединение трансляций в рамках одного дня.
            Учитывает эфирные сутки (05:00-04:59).
            Объединяет смежные сегменты одной программы.

            Args:
                data: pd.DataFrame: датафрейм, для которого будем проводить схлопывание программ.
                type: str: тип таблицы: либо VIMB, либо PALOMARS. Нужно для того, чтобы было легче ориентироваться в столбцах с оригинальными
                "Время начала" и "Время окончания" программ.
                    - Если type == vimb, то делается пометка "Время начала оригинальное vimb". Аналогично с "Время окончания".
                    - Если type == palomars, то делается пометка "Время начала оригинальное palomars". Аналогично с "Время окончания".
        """
        if type not in ['vimb', 'palomars']:
            raise ValueError(f"Недопустимое значение типа таблицы type = {type}. Допустимые значения: 'vimb', 'palomars'")
        
        if data.empty:
            columns = ['Дата', 'Название программы', 'Время выхода', 'Время окончания']
            if include_share:
                columns.append('Share')
                data['Share'] = data['Share'].astype(float)

            return pd.DataFrame(columns = columns)
        
        df = data.copy()

        # Создаем копии столбцов с оригинальными временами выхода и окончания программ
        df[f'Время выхода оригинальное {type}'] = df['Время выхода']
        df[f'Время окончания оригинальное {type}'] = df['Время окончания']
        
        df = self.adjust_end_time(df)
        
        # Функция для сортировки по эфирным суткам
        def broadcast_time_key(time_str):
            h, m, s = map(int, time_str.split(':'))
            return (0 if h >= 5 else 1, h, m, s)
        
        # Преобразование времени в минуты для удобного сравнения
        def time_to_minutes(time_str):
            """
                Преобразует время в формате HH:MM:SS в минуты с начала эфирных суток
            """
            h, m, s = map(int, time_str.split(':'))
            # Для времени до 05:00 добавляем 24 часа
            total_minutes = h * 60 + m + s / 60

            if h < 5:  # Время с 00:00 до 04:59
                total_minutes += 24 * 60  # Добавляем сутки
            return total_minutes
        
        # Подготовка данных
        df['sort_key'] = df['Время выхода'].apply(broadcast_time_key)
        df = df.sort_values(['Дата', 'sort_key'])
        df = df.drop('sort_key', axis = 1)
        
        # Добавляем колонку с временем в минутах для сравнения
        df['start_minutes'] = df['Время выхода'].apply(time_to_minutes)
        df['end_minutes'] = df['Время окончания'].apply(time_to_minutes)


        # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
        # Добавляем колонку с временем в минутах для сравнения
        df['start_minutes_init'] = df[f'Время выхода оригинальное {type}'].apply(time_to_minutes)
        df['end_minutes_init'] = df[f'Время окончания оригинальное {type}'].apply(time_to_minutes)
        # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------
        
        results = []
        
        # Обработка каждой программы
        for program_name in df['Название программы'].unique():
            program_mask = df['Название программы'] == program_name
            program_data = df[program_mask].copy()
            
            if program_data.empty:
                continue
            
            # Сортировка программы по времени (уже отсортирована)
            program_data = program_data.sort_values('start_minutes')
            
            # Объединение сегментов
            current_group = {
                'Дата': program_data.iloc[0]['Дата'],
                'Название программы': program_name,
                f'Название программы {type}': program_data.iloc[0][f'Название программы {type}'],
                'Время выхода': program_data.iloc[0]['Время выхода'],
                'Время окончания': program_data.iloc[0]['Время окончания'],
                'start_minutes': program_data.iloc[0]['start_minutes'],
                'end_minutes': program_data.iloc[0]['end_minutes'],
                # НОВЫЙ КУСОК
                'start_minutes_init': program_data.iloc[0]['start_minutes_init'],
                'end_minutes_init': program_data.iloc[0]['end_minutes_init'],
                f'Время выхода оригинальное {type}': program_data.iloc[0][f'Время выхода оригинальное {type}'],
                f'Время окончания оригинальное {type}': program_data.iloc[0][f'Время окончания оригинальное {type}'],
                # КОНЕЦ НОВОГО КУСКА
            }
            
            if include_share:
                current_group['shares'] = [program_data.iloc[0]['Share']]
                current_group['Жанр'] = program_data.iloc[0]['Жанр']
                if self.channel == 'МатчТВ':
                    current_group['Вид спорта'] = program_data.iloc[0]['Вид спорта']
                    current_group['Метка'] = program_data.iloc[0]['Метка']

            
            # Обработка остальных записей программы
            for i in range(1, len(program_data)):
                current_row = program_data.iloc[i]
                next_start_minutes = current_row['start_minutes']
                next_end_minutes = current_row['end_minutes']

                # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
                next_start_minutes_init = current_row['start_minutes_init']
                next_end_minutes_init = current_row['end_minutes_init']
                # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------
                
                # Проверка на смежность сегментов с учетом разницы в 1 минуту
                time_gap = next_start_minutes - current_group['end_minutes']

                # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
                time_gap_init = next_start_minutes_init - current_group['end_minutes_init']
                # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------
                
                # Ключевое изменение: Не объединяем через границу эфирных суток
                # (кроме специального случая 04:59:59 → 05:00:00)
                prev_end_time = current_group['Время окончания']
                next_start_time = current_row['Время выхода']

                # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
                prev_end_time_init = current_group[f'Время окончания оригинальное {type}']
                next_start_time_init = current_row[f'Время выхода оригинальное {type}']
                # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------
                
                # Проверяем, не пересекаем ли мы границу эфирных суток
                # (следующий сегмент начинается в новых эфирных сутках, а текущий заканчивается в старых)
                crosses_broadcast_day = (
                    prev_end_time >= '00:00:00' and prev_end_time <= '04:59:59' and
                    next_start_time >= '05:00:00'
                )

                # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
                crosses_broadcast_day_init = (
                    prev_end_time_init >= '00:00:00' and prev_end_time_init <= '04:59:59' and
                    next_start_time_init >= '05:00:00'
                )
                # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------
                
                # Условия объединения:
                # 1. Нет разрыва (время окончания = время начала следующей) И не пересекаем границу
                # 2. Разрыв в пределах 1 минуты И не пересекаем границу
                # 3. Специальный случай: 04:59:59 → 05:00:00 (это допускается)
                is_adjacent = (
                    (time_gap == 0 and not crosses_broadcast_day) or  # Нет разрыва и не пересекаем границу
                    (0 < time_gap <= 1 and not crosses_broadcast_day) or  # Разрыв не более 1 минуты и не пересекаем границу
                    (prev_end_time == '04:59:59' and next_start_time == '05:00:00')  # Допустимый переход через границу
                )


                # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
                is_adjacent_init = (
                    (time_gap_init == 0 and not crosses_broadcast_day_init) or  # Нет разрыва и не пересекаем границу
                    (0 < time_gap_init <= 1 and not crosses_broadcast_day_init) or  # Разрыв не более 1 минуты и не пересекаем границу
                    (prev_end_time_init == '04:59:59' and next_start_time_init == '05:00:00')  # Допустимый переход через границу
                )
                # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------
                

                # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
                if is_adjacent and is_adjacent_init:
                    # Объединяем с текущей группой
                    current_group['Время окончания'] = current_row['Время окончания']
                    current_group['end_minutes'] = next_end_minutes

                    # ------------- новый кусок -------------
                    current_group[f'Время окончания оригинальное {type}'] = current_row[f'Время окончания оригинальное {type}']
                    current_group['end_minutes_init'] = next_end_minutes_init
                    # ------------- конец нового куска -------------

                    if include_share:
                        current_group['shares'].append(current_row['Share'])
                        #current_group['Жанр'].append(current_row['Жанр'])
                else:
                    # Сохраняем текущую группу и начинаем новую
                    result_entry = {
                        'Дата': current_group['Дата'],
                        'Название программы': current_group['Название программы'],
                        f'Название программы {type}': current_group[f'Название программы {type}'],
                        'Время выхода': current_group['Время выхода'],
                        'Время окончания': current_group['Время окончания'],
                        f'Время выхода оригинальное {type}': current_group[f'Время выхода оригинальное {type}'],
                        f'Время окончания оригинальное {type}': current_group[f'Время окончания оригинальное {type}']
                    }
                    
                    if include_share:
                        result_entry['Share'] = sum(current_group['shares'])
                        result_entry['Количество_сегментов'] = len(current_group['shares'])
                        result_entry['Жанр'] = current_group['Жанр']
                        if self.channel == 'МатчТВ':
                            result_entry['Вид спорта'] = current_group['Вид спорта']
                            result_entry['Метка'] = current_group['Метка']

                    results.append(result_entry)
                    
                    # Новая группа
                    current_group = {
                        'Дата': current_row['Дата'],
                        'Название программы': program_name,
                        f'Название программы {type}': current_row[f'Название программы {type}'],
                        'Время выхода': current_row['Время выхода'],
                        'Время окончания': current_row['Время окончания'],
                        'start_minutes': next_start_minutes,
                        'end_minutes': next_end_minutes,
                        'start_minutes_init': next_start_minutes_init,
                        'end_minutes_init': next_end_minutes_init,
                        f'Время выхода оригинальное {type}': current_row[f'Время выхода оригинальное {type}'],
                        f'Время окончания оригинальное {type}': current_row[f'Время окончания оригинальное {type}']
                    }
                    
                    if include_share:
                        current_group['shares'] = [current_row['Share']]
                        current_group['Жанр'] = current_row['Жанр']
                        if self.channel == 'МатчТВ':
                            current_group['Вид спорта'] = current_row['Вид спорта']
                            current_group['Метка'] = current_row['Метка']
                # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------

            # -------------- НОВЫЙ КУСОК ДЛЯ ОРИГИНАЛЬНЫХ "ВРЕМЯ ВЫХОДА" И "ВРЕМЯ ОКОНЧАНИЯ" --------------
            # Сохраняем последнюю группу
            result_entry = {
                'Дата': current_group['Дата'],
                'Название программы': current_group['Название программы'],
                f'Название программы {type}': current_group[f'Название программы {type}'],
                'Время выхода': current_group['Время выхода'],
                'Время окончания': current_group['Время окончания'],
                f'Время выхода оригинальное {type}': current_group[f'Время выхода оригинальное {type}'],
                f'Время окончания оригинальное {type}': current_group[f'Время окончания оригинальное {type}']

            }
            # ------------------------------------- КОНЕЦ НОВОГО КУСКА ------------------------------------
            
            if include_share:
                result_entry['Share'] = sum(current_group['shares'])
                result_entry['Количество_сегментов'] = len(current_group['shares'])
                result_entry['Жанр'] = current_group['Жанр']
                if self.channel == 'МатчТВ':
                    result_entry['Вид спорта'] = current_group['Вид спорта']
                    result_entry['Метка'] = current_group['Метка']

            results.append(result_entry)
        
        # Формирование итогового DataFrame
        if not results:
            columns = [
                'Дата', 'Название программы', f'Название программы {type}', 'Время выхода', 
                'Время окончания', f'Время выхода оригинальное {type}', f'Время окончания оригинальное {type}']
            if include_share:
                columns.append('Share')
                columns.append('Жанр')
                if self.channel == 'МатчТВ':
                    columns.append('Вид спорта')
                    columns.append('Метка')

            return pd.DataFrame(columns = columns)
        
        result_df = pd.DataFrame(results)

        result_df['sort_key'] = result_df[f'Время выхода оригинальное {type}'].apply(broadcast_time_key)
        result_df = result_df.sort_values('sort_key').drop('sort_key', axis = 1)
        
        # Выбор нужных колонок
        columns = [
            'Дата', 'Название программы', f'Название программы {type}', 
            'Время выхода', 'Время окончания', 
            f'Время выхода оригинальное {type}', f'Время окончания оригинальное {type}'
            ]
        
        if include_share:
            columns.append('Share')
            columns.append('Жанр')
            if self.channel == 'МатчТВ':
                columns.append('Вид спорта')
                columns.append('Метка')
        
        return result_df[columns].reset_index(drop = True)
    

    def find_matches(self, minutes: int, date):
        """
            Метод для поиска совпадающих программ. 
        """

        vimb_joined = self.join_broadcasts(self.vimb_init, type = 'vimb', include_share = False)
        plmrs_joined = self.join_broadcasts(self.palomars_init, type = 'palomars')

        # Исходная суммарная доля по дню
        share_init = plmrs_joined['Share'].sum()

        # Округление времени
        calculator = TVShareCalculator(self.channel, plmrs_joined)
        plmrs_joined['Время выхода'] = calculator.round_time('Время выхода', minutes)
        plmrs_joined['Время окончания'] = calculator.round_time('Время окончания', minutes)

        plmrs_joined = self.convert_data_column(plmrs_joined)


        # НОВЫЙ КУСОК
        plmrs_joined.rename(columns = {
            #'Название программы palomars': 'Название программы init',
            'Время выхода оригинальное palomars': 'Время выхода init',
            'Время окончания оригинальное palomars': 'Время окончания init'
            },
            inplace = True)

        plmrs_joined = self.fix_invalid_times(plmrs_joined)

        plmrs_joined.rename(columns = {
            'Время выхода init': 'Время выхода оригинальное palomars',
            'Время окончания init': 'Время окончания оригинальное palomars'
            },
            inplace = True)
        # КОНЕЦ НОВОГО КУСКА

        plmrs_joined.rename(columns = 
                            {
                                'Время выхода': 'Время выхода _plmrs', 
                                'Время окончания': 'Время окончания _plmrs'
                             }, 
                             inplace = True)
        
        vimb_joined.rename(columns = 
                           {
                               'Время выхода': 'Время выхода _vimb', 
                               'Время окончания': 'Время окончания _vimb'
                            }, 
                            inplace = True)

        plmrs_joined['Время выхода'] = plmrs_joined['Время выхода _plmrs']
        vimb_joined['Время выхода'] = vimb_joined['Время выхода _vimb']

        # Попытка совместить две сетки между собой
        merged = pd.merge(plmrs_joined, vimb_joined, on = ['Дата', 'Название программы', 'Время выхода'], how = 'left')

        # Создаем финальные столбцы
        merged['Время выхода'] = merged['Время выхода _vimb'].combine_first(merged['Время выхода _plmrs'])
        merged['Время окончания'] = merged['Время окончания _vimb'].combine_first(merged['Время окончания _plmrs'])

 
        # Удаляем ненужные столбцы
        result = merged.drop(columns = ['Время выхода _plmrs', 'Время окончания _plmrs', 
                            'Время выхода _vimb', 'Время окончания _vimb'])
        

        if self.channel != 'МатчТВ':
            result = result[
                [
                    'Дата', 'Название программы', 'Время выхода', 'Время окончания', 'Share', 
                    'Название программы vimb', 'Название программы palomars',
                    'Время выхода оригинальное vimb', 'Время окончания оригинальное vimb',
                    'Время выхода оригинальное palomars', 'Время окончания оригинальное palomars',
                    'Жанр'
                ]
                ].reset_index(drop = True)
        else:
            result = result[
                [
                    'Дата', 'Название программы', 'Время выхода', 'Время окончания', 'Share', 
                    'Название программы vimb', 'Название программы palomars',
                    'Время выхода оригинальное vimb', 'Время окончания оригинальное vimb',
                    'Время выхода оригинальное palomars', 'Время окончания оригинальное palomars',
                    'Жанр', 'Вид спорта', 'Метка'
                ]
                ].reset_index(drop = True)

 
        result.rename(columns = {
            'Название программы palomars': 'Название программы init',
            'Время выхода оригинальное palomars': 'Время выхода init',
            'Время окончания оригинальное palomars': 'Время окончания init'
            },
            inplace = True)
        
        # Первая коррекция времен
        #result = self.fix_invalid_times(result)
        
        # Заменяем значения начиная со второго
        for i in range(1, len(result)):
            
            # Корректируем время окончания предыдущей программы
            if result.loc[i - 1, 'Время окончания'] != result.loc[i, 'Время выхода']:
                #result.loc[i - 1, 'Время окончания'] = result.loc[i, 'Время выхода']
                result.loc[i, 'Время выхода'] = result.loc[i - 1, 'Время окончания']

        if self.channel != 'МатчТВ':
            result = result[
                [
                    'Дата', 'Название программы', 
                    'Время выхода init', 'Время окончания init',
                    'Share', 'Название программы init',
                    'Жанр'
                ]
            ]
        else:
            result = result[
                [
                    'Дата', 'Название программы', 
                    'Время выхода init', 'Время окончания init',
                    'Share', 'Название программы init',
                    'Жанр', 'Вид спорта', 'Метка'
                ]
            ]

        result.rename(
            columns = {
                'Время выхода init': 'Время выхода',
                'Время окончания init': 'Время окончания',
            },
            inplace = True
            )

        # Вспомогательная функция для проверки соответствия времени
        def check_time_intervals(df):
            """
                Проверяет соответствие времен окончания и начала соседних программ
                Возвращает список некорректных записей
            """
            issues = []
            
            for i in range(len(df) - 1):
                current_end = df.iloc[i]['Время окончания']
                next_start = df.iloc[i + 1]['Время выхода']
                
                if current_end != next_start:
                    issue = {
                        'индекс': i,
                        'дата': df.iloc[i]['Дата'],
                        'программа': df.iloc[i]['Название программы'],
                        'время_окончания': current_end,
                        'следующая_программа': df.iloc[i + 1]['Название программы'],
                        'время_начала': next_start,
                        'проблема': f"Время окончания '{current_end}' не равно времени начала следующей программы '{next_start}'"
                    }
                    issues.append(issue)
            
            return issues

        result = self.adjust_end_time(result)
        result.drop_duplicates(keep = 'first', inplace = True)

        duplicated_values = result[result['Время выхода'].duplicated(keep = False)]
        if len(duplicated_values):
            print(
                Color.BOLD + Color.MAROON + \
                f'❗ ВНИМАНИЕ: Для канала {self.channel} и даты {date} не все значения в столбце "Время выхода" уникальные! Пожалуйста, сделайте проверку' + \
                Color.END
            )
        
        share_end = result['Share'].sum()
 
        if share_init != share_end:
            print(Color.BOLD + Color.RED + f'‼️ Нужен дополнительный анализ! Доля для {date} после схлопывания оказалась неверной.' + Color.END)
        
        return result


class MonthlyShareAnalyzer:
    """
    (!!!) ВАЖНО (!!!) Работает для какого-то конкретного месяца и года!
        Класс по расчёту месячной доли через TTV и количество дней в месяце. 

        Итоговая доля считается как TVR_summ (будни) + TVR_summ (выходные) / TVR_summ (за месяц), где

        TVR_summ (будни) = Средняя доля будние * TTV (будни) * Кол-во будних дней в месяце
        TVR_summ (выходные) = Средняя доля выходные * TTV (выходные) * Кол-во выходные дней в месяце
        TVR_summ (за месяц) = TTV (за месяц) * Кол-во дней в месяце
    """
    def __init__(self, year: int, month: str, bca: str, forecast_df: pd.DataFrame):
        self.year = year
        self.month = month
        self.bca = bca
        self.forecast_df = forecast_df
    

    @staticmethod
    def get_day_type(date, holidays: list, working_saturdays: list):
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

    def calculate_ttv(self, ttv_filepath: str, debug=False):
        """
        Чтение TTV из файла Excel с автоматическим определением года.

        Параметры:
            ttv_filepath (str): путь к файлу Excel
            debug (bool): печатать отладочную информацию
        """

        try:
            df_raw = pd.read_excel(
                ttv_filepath,
                sheet_name='Для шаблонов',
                skiprows=3,  # пропускаем служебные строки с формулами
                header=None,  # заголовки будут в первой прочитанной строке
                nrows=39
            )
        except FileNotFoundError:
            raise FileNotFoundError(f'Файл не найден: {ttv_filepath}')

        # Строка заголовков (исходная строка 4)
        header_row = df_raw.iloc[0]

        # Поиск колонки с нужным годом
        year_col = None
        for idx, val in enumerate(header_row):
            if pd.notna(val) and str(val).strip() == str(self.year):
                year_col = idx
                break
        if year_col is None:
            raise ValueError(f'Год {self.year} не найден в строке заголовков')

        # Определение диапазона колонок для данных этого года
        start_col = None
        for idx in range(year_col + 1, len(header_row)):
            if pd.notna(header_row[idx]) and str(header_row[idx]).strip() != '':
                start_col = idx
                break
        if start_col is None:
            raise ValueError(f'Не найдены колонки с данными для года {self.year}')

        # Ищем следующий год
        end_col = len(header_row)
        for idx in range(start_col + 1, len(header_row)):
            val = header_row[idx]
            if pd.notna(val):
                val_str = str(val).strip()
                if val_str.isdigit() and len(val_str) == 4 and val_str != str(self.year):
                    end_col = idx
                    break

        data_cols = list(range(start_col, end_col))
        metric_names = [
            str(header_row[col]).strip().lower() if pd.notna(header_row[col]) else f'col_{col}'
            for col in data_cols
        ]
        df_data = df_raw.iloc[:, data_cols].copy()
        df_data.columns = metric_names


        df_data['Месяц'] = df_raw.iloc[:, 2].values
        df_data['Тип'] = ''
        # Индексы строк (0 – заголовок, 1–12 – ВСЕГО, 14–25 – Будни, 27–38 – Выходные)
        df_data.loc[1:12, 'Тип'] = 'ВСЕГО'
        df_data.loc[14:25, 'Тип'] = 'Будни'
        df_data.loc[27:38, 'Тип'] = 'Выходные'

        month_clean = self.month.strip().lower()
        mask_month = df_data['Месяц'].astype(str).str.lower() == month_clean
        month_rows = df_data[mask_month]

        if month_rows.empty:
            raise ValueError(f'Месяц "{self.month}" не найден в данных')

        target = self.bca.strip().lower()
        if target not in df_data.columns:
            raise ValueError(f'Метрика "{self.bca}" не найдена в заголовках')

        full_row = month_rows[month_rows['Тип'] == 'ВСЕГО']
        weekday_row = month_rows[month_rows['Тип'] == 'Будни']
        weekend_row = month_rows[month_rows['Тип'] == 'Выходные']

        if full_row.empty or weekday_row.empty or weekend_row.empty:
            raise ValueError(f'Не хватает данных для месяца {self.month} (возможно, отсутствует тип ВСЕГО/Будни/Выходные)')

        result = {
            'итого': full_row.iloc[0][target],
            'будни': weekday_row.iloc[0][target],
            'выходные': weekend_row.iloc[0][target]
        }

        if debug:
            print(f'Данные для {self.month} {self.year} ({self.bca}): {result}')

        return result
    

    def calculate_monthly_share(self, ttv_dict: dict, work_saturdays, all_holidays, debug = False):
        """
            Метод для расчета месячной доли 
        """
        # 2. Определение типа дня
        self.df['Тип дня'] = self.df['Дата'].apply(lambda x: MonthlyShareAnalyzer.get_day_type(x, all_holidays, work_saturdays))

        # 3. Отбор будних и выходных дней
        weekdays = self.df[self.df['Тип дня'] == 'будний'].reset_index(drop = True)
        weekends = self.df[self.df['Тип дня'] == 'выходной'].reset_index(drop = True)

        # 4. Подсчет средней доли будних и выходных дней
        mean_share_weekdays = np.mean(list(weekdays['Share']))
        mean_share_weekend = np.mean(list(weekends['Share']))

        if debug:
            print(f'Средняя доля будних: {np.round(mean_share_weekdays, 3)}, Средняя доля выходных: {np.round(mean_share_weekend, 3)}')

        # 5. Подсчет количества будних, выходных и количества дней в месяце
        count_weekends = (self.df['Тип дня'] == 'выходной').sum()
        count_weekdays = (self.df['Тип дня'] == 'будний').sum()
        n_days = count_weekends + count_weekdays
        
        if debug:
            print(f'Кол-во будних: {count_weekdays}, Кол-во выходных: {count_weekends}')

        TVR_summ = {
            'итого': ttv_dict['итого'] * n_days,
            'будни': mean_share_weekdays * ttv_dict['будни'] * count_weekdays,
            'выходные': mean_share_weekend * ttv_dict['выходные'] * count_weekends
            }
        
        if debug:
            print(TVR_summ)

        share_per_month = (TVR_summ['будни'] + TVR_summ['выходные']) / TVR_summ['итого']
        return share_per_month
    

    def fit_calculate(
            self, 
            ttv_filepath: str, 
            target_column: str, 
            need_columns: list, 
            work_saturdays, 
            all_holidays,
            debug = False
        ):
        """
            Пайплайн для расчета
        """
        unique_dates = self.forecast_df['Дата'].unique()

        shares = {}
        for date in unique_dates:
            df = self.forecast_df[self.forecast_df['Дата'] == date].reset_index(drop = True)
            
            # Расчёт суммарной доли по дню
            shares[date] = np.sum(list(df[target_column]))
        
        self.df = pd.DataFrame({
            'Дата': pd.to_datetime(list(shares.keys())),
            'Share': list(shares.values())
        })

        # Сортировка по дате (если нужно)
        self.df = self.df.sort_values('Дата').reset_index(drop = True)

        ttv = self.calculate_ttv(ttv_filepath, debug = debug)
        share_per_month = self.calculate_monthly_share(ttv, work_saturdays, all_holidays, debug = debug)
        return share_per_month

