// Feb. 2020:" This version of code uses new fiber optic board with different output channels connected to the four amplifier boards.  BoardMap variable has been updated.  

#include "hardware.h"
#include "util.h"
#include "t3spi.h"

//calibration data

bool should_next = false;

volatile int counter = 0;
volatile int cint = 0;

volatile long tHigh = 0;
volatile long tIrupt = 0;
volatile bool outHigh = false;
volatile int t;


const int ctrlBuffer_length = 100;
char ctrlBuffer[ctrlBuffer_length];

int8_t channels_used[NUM_B][NUM_C]  =
{
  {0, 1, 2, 3, 4, 5, 6, 7}
//  {0, 1, 2, 3, 4, 5, 6, 7},
//  {0, 1, 2, 3, 4, 5, 6, 7},
//  {0, 1, 2, 3, 4, 5, 6, 7}
};

/******************************************************/
/*********************** IRUPT ************************/
/******************************************************/
long timey;
bool first = 0;
void setDACVal() {
  int repIdx;
  int blkIdx = computeBlockIdx(counter);
  if (blkIdx == -1) {
    blkIdx = 0; repIdx = 0;
    counter = 0;
  } else {
    repIdx = computeRepIdx(counter, blkIdx);
  }
  Serial.print("counter: ");
  Serial.println(counter);
  Serial.print("blkIdx: ");
  Serial.println(blkIdx);
  Serial.print("repIdx: ");
  Serial.println(repIdx);
  update_outputs(blkIdx, repIdx);
  counter++;
  // optional BNC output July 2019
     if(bncOut){
        long tnow = millis();
        if(tnow > tIrupt && !outHigh){
          tHigh = tnow;
          digitalWrite(bncPin,HIGH);
    ////      Serial.println("High");
          outHigh = true;
          tIrupt = tnow;

        }

       tnow = millis();
        if(tnow > tIrupt + .013  && outHigh){
          digitalWrite(bncPin,LOW);
    ////      Serial.println("High");
          outHigh = false;
          tIrupt = tnow;

        }


        
      }
      long timeytmp = millis();
    ////  Serial.println(timeytmp-timey);
      timey = timeytmp;
  // end bnc optional segment

  //  if (counter <= 0) {
  //    counter = 39;
  //  }
}

//}

typedef enum read_mode {MODE_ACCEPT, MODE_HEADER, MODE_BODY} read_mode;
read_mode mode;
/******************************************************/
/*********************** SETUP ************************/
/******************************************************/

void setup() {
  mode = MODE_ACCEPT;
  read_in_flight = false;
  Serial.begin(115200);

  //SETUP board and function select 
  initIO();
  selectNone();
  spiInit();

  //Initialize calibration data
  for (int b = 0; b < NUM_B; b++) {
    selectBoard(b);
    for (int c = 0; c < NUM_C; c++) {
      zeroPoint[b][c] = 0;
      gain[b][c] = -1.6;
      //      LTC2656Write(WRITE_AND_UPDATE,channelMap[c],32768);
    }
  }


  for (int j = 0; j < NUM_B * NUM_C; j++) {
    channel_order[j] = -1;
    board_order[j] = -1;
  }
  int i = 0;
  for (int b = 0; b < NUM_B; b++) {
    for (int c = 0; c < NUM_C; c++) {
      if (channels_used[b][c] != -1) {
        channel_order[i] = channels_used[b][c];
        board_order[i] = b;
        i = i + 1;
      } else {
        break;
      }
    }
  }

  delay(500);
   // attachInterrupt(interruptPin, setDACVal, FALLING);  // <===== Uncomment this line to enable TRIGGERING
       
  //    attachInterrupt(4, setDACVal, RISING);
  Serial.println("I'm up");
  //  selectBoard(4);

  delay(100);
  NVIC_ENABLE_IRQ(IRQ_SPI1);
  t = micros();
  Serial.println("about to write");
  //  zero_all();
  Serial.println("finished write");
  delay(500);
  SPI_SLAVE->packetCT = 0;
  SPI_SLAVE->dataPointer = 0;

  //  Serial.println(measure_gain(2,0),1);

  Serial.println("lets do it");
  //  selectBoard(4);
  //  zero_all();
  selectBoard(4);
  delay(100);
  Serial.println(NUM_B);
  Serial.println(NUM_C);
  selectNone();
  compute_transitions_base();
}


/******************************************************/
/*********************** MAIN LOOP ********************/
/******************************************************/



void loop() {
  //  if(bncOut & outHigh){
  //    long tnow = millis();
  //    if(tnow > tHigh + 100){
  //       digitalWrite(bncPin,LOW);
  //       outHigh = false;
  //       Serial.println("low");
  //    }
  //  }
  if (should_next) {
    //    zero_all();
    //    calibrate_all();
    //    update_outputs(counter);
    should_next = false;
    print_all();
  }
  switch (mode) {
    case MODE_ACCEPT:
      char incomingByte;
      char boardStr[3];
      char channelStr[3];
      char floatStr[20]; // Assuming the maximum length of the float string is 20 characters
      char buffer[64]; // Assuming the maximum length of the input line is 64 characters
      char ch;
      int in;
      if (Serial.available() > 0) {
        incomingByte = Serial.read();
        Serial.print(incomingByte);
        switch (incomingByte) {
          case 1:
            counter = 0;
            mode = MODE_HEADER;
          case 'Z':  // zero all the currents immediately
            zero_all();
            Serial.println("\nDone Zeroing");
            break;
          case 'C':
            for (int i = 0; i < NUM_C; i++) {
              Serial.println("calibrating a channel");
              calibrate_channel(0, i);
              Serial.println("calibrated a channel");
            }
            Serial.println("Done Calibrating");
            break;
          case 'D':
            for (int i = 0; i < 1; i++) {
              calibrate_channel(0, i);
            }
            break;
          case 'T':
            setDACVal(); // advance to next row of shim currents 
            break;
          case 'I':   // display current on each channel from ADC.  Only reads over the range -1.2A to 1.2A
            print_all();
            Serial.println("Done Printing Currents");
            break;
          case 'A':
            print_all_boards();
            break;
          case 'S':
            selectBoard(0);
            LTC2656Write(WRITE_AND_UPDATE, channelMap[0], computeDacVal_I(0.5, 0, 0));
            break;
          case 'M':  // load first row of shims
            counter = 0;
            cint = 0;
            should_next = false;
            Serial.println(counter);
            update_outputs(0, 0);
            counter += 1;
          case 'X': // Initiate instruction reading

            Serial.read();
            // Read the sequence until newline or buffer limit
            in = 0;
            while (Serial.available() > 0) {
              ch = Serial.read(); // Read a character from serial input
              buffer[in++] = ch; // Store the character in the buffer
            }
            buffer[in] = '\0'; // Null-terminate the buffer

            // Parse the sequence

            sscanf(buffer, "%2s %2s %19s", boardStr, channelStr, floatStr);

            Serial.println();
            Serial.print("board: ");
            Serial.print(boardStr);
            Serial.print("; channel: ");
            Serial.print(channelStr);
            Serial.print("; value: ");
            Serial.println(atof(floatStr));

            selectBoard(0);
            LTC2656Write(WRITE_AND_UPDATE, channelMap[atoi(channelStr)], computeDacVal_I(atof(floatStr), atoi(boardStr), atoi(channelStr)));
            Serial.println("Done Setting Current");
            Serial.println();

            break;
        }
      }
      break;
    case MODE_HEADER:
      if (Serial.available()) {
        int ctrlBuff_readlen =
          Serial.readBytesUntil(0, ctrlBuffer, ctrlBuffer_length);
        read_ctrl_string(ctrlBuffer);
        mode = MODE_BODY;
      }
      break;
    case MODE_BODY:
      if (read_float_dump()) {
        mode = MODE_ACCEPT;
      }
      break;
  }


}
